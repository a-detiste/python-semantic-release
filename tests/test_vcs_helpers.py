import os
from datetime import date

import git
import pytest
from git import GitCommandError, Repo, TagObject

from semantic_release.errors import GitError, HvcsRepoParseError
from semantic_release.history import get_release_version_pattern, get_version_pattern
from semantic_release.vcs_helpers import (
    checkout,
    commit_new_version,
    get_commit_log,
    get_current_head_hash,
    get_last_version,
    get_repository_owner_and_name,
    push_new_version,
    tag_new_version,
    update_additional_files,
    update_changelog_file,
)

from . import mock, wrapped_config_get


@pytest.fixture
def mock_git(mocker):
    return mocker.patch("semantic_release.vcs_helpers._repo.git")


def test_first_commit_is_not_initial_commit():
    assert next(get_commit_log()) != "Initial commit"


@pytest.mark.parametrize(
    "params",
    [
        # Basic usage:
        dict(
            version="1.0.0",
            config=dict(
                version_variable="path:---",
            ),
            add_paths=[
                "path",
            ],
            commit_args=dict(
                m="1.0.0\n\nAutomatically generated by python-semantic-release",
                author="semantic-release <semantic-release>",
            ),
        ),
        # With author:
        dict(
            version="1.0.0",
            config=dict(
                version_variable="path:---",
                commit_author="Alice <alice@example.com>",
            ),
            add_paths=[
                "path",
            ],
            commit_args=dict(
                m="1.0.0\n\nAutomatically generated by python-semantic-release",
                author="Alice <alice@example.com>",
            ),
        ),
        # With multiple version paths:
        dict(
            version="1.0.0",
            config=dict(
                version_variable=[
                    "path1:---",
                    "path2:---",
                ]
            ),
            add_paths=[
                "path1",
                "path2",
            ],
            commit_args=dict(
                m="1.0.0\n\nAutomatically generated by python-semantic-release",
                author="semantic-release <semantic-release>",
            ),
        ),
    ],
)
def test_add_and_commit(mock_git, mocker, params):
    mocker.patch(
        "semantic_release.vcs_helpers.config.get",
        wrapped_config_get(**params["config"]),
    )

    commit_new_version(params["version"])

    for path in params["add_paths"]:
        mock_git.add.assert_any_call(path)

    mock_git.commit.assert_called_once_with(**params["commit_args"])


def test_tag_new_version(mock_git, mocker):
    mocker.patch(
        "semantic_release.vcs_helpers.config.get",
        return_value="ver{version}",
    )
    tag_new_version("1.0.0")
    mock_git.tag.assert_called_with("-a", "ver1.0.0", m="ver1.0.0")


def test_push_new_version(mock_git):
    push_new_version()
    mock_git.push.assert_has_calls(
        [
            mock.call("origin", "master"),
            mock.call("--tags", "origin", "master"),
        ]
    )


def test_push_new_version_with_custom_branch(mock_git):
    push_new_version(branch="release")
    mock_git.push.assert_has_calls(
        [
            mock.call("origin", "release"),
            mock.call("--tags", "origin", "release"),
        ]
    )


@pytest.mark.parametrize("actor", (None, "GITHUB_ACTOR_TOKEN"))
def test_push_using_token(mock_git, mocker, actor):
    mocker.patch.dict(os.environ, {"GITHUB_ACTOR": actor} if actor else {}, clear=True)
    token = "auth--token"
    domain = "domain"
    owner = "owner"
    name = "name"
    branch = "main"
    push_new_version(
        auth_token=token, domain=domain, owner=owner, name=name, branch=branch
    )
    server = (
        f"https://{actor + ':' if actor else ''}{token}@{domain}/{owner}/{name}.git"
    )
    mock_git.push.assert_has_calls(
        [
            mock.call(server, branch),
            mock.call("--tags", server, branch),
        ]
    )


def test_push_ignoring_token(mock_git, mocker):
    mocker.patch(
        "semantic_release.vcs_helpers.config.get",
        wrapped_config_get(**{"ignore_token_for_push": True}),
    )
    token = "auth--token"
    domain = "domain"
    owner = "owner"
    name = "name"
    branch = "main"
    push_new_version(
        auth_token=token, domain=domain, owner=owner, name=name, branch=branch
    )
    server = "origin"
    mock_git.push.assert_has_calls(
        [
            mock.call(server, branch),
            mock.call("--tags", server, branch),
        ]
    )


@mock.patch.dict(
    os.environ,
    {
        k: v
        for k, v in os.environ.items()
        if k not in ["GITHUB_REPOSITORY", "CI_PROJECT_NAMESPACE", "CI_PROJECT_NAME"]
    },
    clear=True,
)
@pytest.mark.parametrize(
    "origin_url,expected_result",
    [
        ("git@github.com:group/project.git", ("group", "project")),
        ("git@gitlab.example.com:group/project.git", ("group", "project")),
        (
            "git@gitlab.example.com:group/subgroup/project.git",
            ("group/subgroup", "project"),
        ),
        (
            "git@gitlab.example.com:group/subgroup/project",
            ("group/subgroup", "project"),
        ),
        (
            "git@gitlab.example.com:group/subgroup.with.dots/project",
            ("group/subgroup.with.dots", "project"),
        ),
        ("https://github.com/group/project.git", ("group", "project")),
        (
            "https://gitlab.example.com/group/subgroup/project.git",
            ("group/subgroup", "project"),
        ),
        (
            "https://gitlab.example.com/group/subgroup/project",
            ("group/subgroup", "project"),
        ),
        (
            "https://gitlab.example.com/group/subgroup/pro.ject",
            ("group/subgroup", "pro.ject"),
        ),
        (
            "https://gitlab.example.com/group/subgroup/pro.ject.git",
            ("group/subgroup", "pro.ject"),
        ),
        (
            "https://gitlab.example.com/firstname.lastname/project.git",
            ("firstname.lastname", "project"),
        ),
        (
            "https://gitlab-ci-token:MySuperToken@gitlab.example.com/group/project.git",
            ("group", "project"),
        ),
        (
            "https://gitlab-ci-token:MySuperToken@gitlab.example.com/group/subgroup/project.git",
            ("group/subgroup", "project"),
        ),
        (
            "https://gitlab-ci-token:MySuperToken@gitlab.example.com/group/sub.group/project.git",
            ("group/sub.group", "project"),
        ),
        ("bad_repo_url", HvcsRepoParseError),
    ],
)
def test_get_repository_owner_and_name(mocker, origin_url, expected_result):
    class FakeRemote:
        url = origin_url

    mocker.patch("git.repo.base.Repo.remote", return_value=FakeRemote())
    if isinstance(expected_result, tuple):
        assert get_repository_owner_and_name() == expected_result
    else:
        with pytest.raises(expected_result):
            get_repository_owner_and_name()


@mock.patch.dict(
    os.environ,
    {
        **os.environ,
        "GITHUB_REPOSITORY": "group/subgroup/project",
    },
    clear=True,
)
@pytest.mark.parametrize(
    "origin_url,expected_result",
    [
        ("https://github.com/group/project.git", ("group/subgroup", "project")),
        (
            "https://github.com/group/subgroup/project.git",
            ("group/subgroup", "project"),
        ),
        (
            "https://github.com/group/sub.group/project.git",
            ("group/subgroup", "project"),
        ),
        (
            "https://github.com/group/subgroup/pro.ject.git",
            ("group/subgroup", "project"),
        ),
    ],
)
def test_get_repository_owner_and_name_github(mocker, origin_url, expected_result):
    class FakeRemote:
        url = origin_url

    mocker.patch("git.repo.base.Repo.remote", return_value=FakeRemote())
    if isinstance(expected_result, tuple):
        assert get_repository_owner_and_name() == expected_result
    else:
        with pytest.raises(expected_result):
            get_repository_owner_and_name()


@mock.patch.dict(
    os.environ,
    {
        **os.environ,
        "CI_PROJECT_NAMESPACE": "group/subgroup",
        "CI_PROJECT_NAME": "project",
    },
    clear=True,
)
@pytest.mark.parametrize(
    "origin_url,expected_result",
    [
        (
            "https://gitlab.example.com/group/subgroup/project.git",
            ("group/subgroup", "project"),
        ),
        (
            "https://gitlab.example.com/group/subgroup/project",
            ("group/subgroup", "project"),
        ),
        ("https://gitlab.example.com/group/project", ("group/subgroup", "project")),
        (
            "https://gitlab-ci-token:MySuperToken@gitlab.example.com/group/subgroup/project.git",
            ("group/subgroup", "project"),
        ),
    ],
)
def test_get_repository_owner_and_name_gitlab(mocker, origin_url, expected_result):
    class FakeRemote:
        url = origin_url

    mocker.patch("git.repo.base.Repo.remote", return_value=FakeRemote())
    if isinstance(expected_result, tuple):
        assert get_repository_owner_and_name() == expected_result
    else:
        with pytest.raises(expected_result):
            get_repository_owner_and_name()


def test_get_current_head_hash(mocker):
    mocker.patch("git.objects.commit.Commit.name_rev", "commit-hash branch-name")
    assert get_current_head_hash() == "commit-hash"


def test_push_should_not_print_auth_token(mock_git, mocker):
    mock_git.configure_mock(
        **{
            "push.side_effect": GitCommandError(
                "auth--token", 1, b"auth--token", b"auth--token"
            )
        }
    )
    mocker.patch(
        "semantic_release.vcs_helpers.config.get",
        wrapped_config_get(**{"hvcs": "gitlab"}),
    )
    with pytest.raises(GitError) as excinfo:
        push_new_version(auth_token="auth--token")
    assert "auth--token" not in str(excinfo)


def test_checkout_should_checkout_correct_branch(mock_git):
    checkout("a-branch")
    mock_git.checkout.assert_called_once_with("a-branch")


@pytest.mark.parametrize(
    "pattern, skip_tags,expected_result",
    [
        ("(\d+.\d+.\d+)", None, "2.0.0"),
        ("(\d+.\d+.\d+)", ["v2.0.0"], "1.1.0"),
        ("(\d+.\d+.\d+)", ["v0.1.0", "v1.0.0", "v1.1.0", "v2.0.0"], None),
    ],
)
def test_get_last_version(pattern, skip_tags, expected_result):
    class FakeCommit:
        def __init__(self, com_date):
            self.committed_date = com_date

    class FakeTagObject:
        def __init__(self, tag_date):
            self.tagged_date = tag_date

    class FakeTag:
        def __init__(self, name, sha, date, is_tag_object):
            self.name = name
            self.tag = FakeTagObject(date)
            if is_tag_object:
                self.commit = TagObject(Repo(), sha)
            else:
                self.commit = FakeCommit(date)

    mock.patch("semantic_release.vcs_helpers.check_repo")
    git.repo.base.Repo.tags = mock.PropertyMock(
        return_value=[
            FakeTag("v0.1.0", "aaaaaaaaaaaaaaaaaaaa", 1, True),
            FakeTag("v2.0.0", "dddddddddddddddddddd", 4, True),
            FakeTag("badly_formatted", "eeeeeeeeeeeeeeeeeeee", 5, False),
            FakeTag("v1.1.0", "cccccccccccccccccccc", 3, True),
            FakeTag("v1.0.0", "bbbbbbbbbbbbbbbbbbbb", 2, False),
        ]
    )
    assert expected_result == get_last_version(pattern, skip_tags)


@pytest.mark.parametrize(
    "get_pattern, skip_tags,expected_result",
    [
        (get_version_pattern, None, "2.1.0-beta.0"),
        (get_version_pattern, ["v2.1.0-beta.0"], "2.0.0"),
        (get_version_pattern, ["v2.1.0-beta.0", "v2.0.0-beta.0", "v2.0.0"], "1.1.0"),
        (
            get_version_pattern,
            ["v2.1.0-beta.0", "v2.0.0-beta.0", "v0.1.0", "v1.0.0", "v1.1.0", "v2.0.0"],
            None,
        ),
        (get_release_version_pattern, None, "2.0.0"),
        (get_release_version_pattern, ["v2.0.0"], "1.1.0"),
        (get_release_version_pattern, ["v2.0.0", "v1.1.0", "v1.0.0", "v0.1.0"], None),
    ],
)
def test_get_last_version_with_real_pattern(get_pattern, skip_tags, expected_result):
    # TODO: add some prerelease tags
    class FakeCommit:
        def __init__(self, com_date):
            self.committed_date = com_date

    class FakeTagObject:
        def __init__(self, tag_date):
            self.tagged_date = tag_date

    class FakeTag:
        def __init__(self, name, sha, date, is_tag_object):
            self.name = name
            self.tag = FakeTagObject(date)
            if is_tag_object:
                self.commit = TagObject(Repo(), sha)
            else:
                self.commit = FakeCommit(date)

    mock.patch("semantic_release.vcs_helpers.check_repo")
    git.repo.base.Repo.tags = mock.PropertyMock(
        return_value=[
            FakeTag("v0.1.0", "aaaaaaaaaaaaaaaaaaaa", 1, True),
            FakeTag("v2.0.0", "dddddddddddddddddddd", 5, True),
            FakeTag("v2.1.0-beta.0", "ffffffffffffffffffff", 7, True),
            FakeTag("badly_formatted", "eeeeeeeeeeeeeeeeeeee", 6, False),
            FakeTag("v2.0.0-beta.0", "ffffffffffffffffffff", 4, True),
            FakeTag("v1.1.0", "cccccccccccccccccccc", 3, True),
            FakeTag("v1.0.0", "bbbbbbbbbbbbbbbbbbbb", 2, False),
        ]
    )
    assert expected_result == get_last_version(get_pattern(), skip_tags)


def test_update_changelog_file_ok(mock_git, mocker):
    initial_content = (
        "# Changelog\n"
        "\n"
        "<!--next-version-placeholder-->\n"
        "\n"
        "## v1.0.0 (2015-08-04)\n"
        "### Feature\n"
        "* Just a start"
    )
    mocker.patch("semantic_release.vcs_helpers.Path.exists", return_value=True)
    mocked_read_text = mocker.patch(
        "semantic_release.vcs_helpers.Path.read_text", return_value=initial_content
    )
    mocked_write_text = mocker.patch("semantic_release.vcs_helpers.Path.write_text")

    content_to_add_str = "### Fix\n* Fix a bug\n### Feature\n* Add something awesome"
    update_changelog_file("2.0.0", content_to_add_str)

    mock_git.add.assert_called_once_with("CHANGELOG.md")
    mocked_read_text.assert_called()
    expected_content_str = (
        "# Changelog\n"
        "\n"
        "<!--next-version-placeholder-->\n"
        "\n"
        f"## v2.0.0 ({date.today():%Y-%m-%d})\n"
        "### Fix\n"
        "* Fix a bug\n"
        "### Feature\n"
        "* Add something awesome\n"
        "\n"
        "## v1.0.0 (2015-08-04)\n"
        "### Feature\n"
        "* Just a start"
    )
    mocked_write_text.assert_called_once_with(expected_content_str)


def test_update_changelog_file_missing_file(mock_git, mocker):
    mocker.patch("semantic_release.vcs_helpers.Path.exists", return_value=False)
    mocked_read_text = mocker.patch("semantic_release.vcs_helpers.Path.read_text")
    mocked_write_text = mocker.patch("semantic_release.vcs_helpers.Path.write_text")

    update_changelog_file("2.0.0", "* Some new content")

    mock_git.add.assert_called_once_with("CHANGELOG.md")
    mocked_read_text.assert_not_called()
    mocked_write_text.assert_called_once_with(
        "# Changelog\n"
        "\n"
        "<!--next-version-placeholder-->\n"
        "\n"
        f"## v2.0.0 ({date.today():%Y-%m-%d})\n"
        "* Some new content\n"
    )


def test_update_changelog_file_missing_placeholder_but_containing_header(
    mock_git, mocker
):
    mocker.patch("semantic_release.vcs_helpers.Path.exists", return_value=True)
    mocker.patch(
        "semantic_release.vcs_helpers.Path.read_text", return_value="# Changelog"
    )
    mocked_write_text = mocker.patch("semantic_release.vcs_helpers.Path.write_text")

    update_changelog_file("2.0.0", "* Some new content")

    mock_git.add.assert_called_once_with("CHANGELOG.md")
    mocked_write_text.assert_called_once_with(
        "# Changelog\n"
        "\n"
        "<!--next-version-placeholder-->\n"
        "\n"
        f"## v2.0.0 ({date.today():%Y-%m-%d})\n"
        "* Some new content\n"
    )


def test_update_changelog_empty_file(mock_git, mocker):
    mocker.patch("semantic_release.vcs_helpers.Path.exists", return_value=True)
    mocker.patch("semantic_release.vcs_helpers.Path.read_text", return_value="")
    mocked_write_text = mocker.patch("semantic_release.vcs_helpers.Path.write_text")

    update_changelog_file("2.0.0", "* Some new content")

    mock_git.add.assert_called_once_with("CHANGELOG.md")
    mocked_write_text.assert_called_once_with(
        "# Changelog\n"
        "\n"
        "<!--next-version-placeholder-->\n"
        "\n"
        f"## v2.0.0 ({date.today():%Y-%m-%d})\n"
        "* Some new content\n"
    )


def test_update_changelog_file_missing_placeholder(mock_git, mocker):
    mocker.patch("semantic_release.vcs_helpers.Path.exists", return_value=True)
    mocked_read_text = mocker.patch(
        "semantic_release.vcs_helpers.Path.read_text", return_value="# Uknown header"
    )
    mocked_write_text = mocker.patch("semantic_release.vcs_helpers.Path.write_text")

    update_changelog_file("2.0.0", "* Some new content")

    mock_git.add.assert_not_called()
    mocked_read_text.assert_called()
    mocked_write_text.assert_not_called()


@pytest.mark.parametrize(
    "include_additional_files",
    [
        "",
        ",",
        "somefile.txt",
        "somefile.txt,anotherfile.rst",
        "somefile.txt,anotherfile.rst,finalfile.md",
    ],
)
def test_update_additional_files_with_no_changes(
    mock_git,
    mocker,
    include_additional_files,
):
    """
    Since we have no file changes, we expect `add` to never be called,
    regardless of the config.
    """
    mocker.patch(
        "semantic_release.vcs_helpers.config.get",
        wrapped_config_get(**{"include_additional_files": include_additional_files}),
    )
    mocker.patch("semantic_release.vcs_helpers.get_changed_files", return_value=[])
    update_additional_files()
    mock_git.add.assert_not_called()


def test_update_additional_files_single_changed_file(mock_git, mocker):
    """
    We expect to add the single file corresponding to config & changes.
    """
    mocker.patch(
        "semantic_release.vcs_helpers.config.get",
        wrapped_config_get(**{"include_additional_files": "somefile.txt"}),
    )
    mocker.patch(
        "semantic_release.vcs_helpers.get_changed_files",
        return_value=["somefile.txt"],
    )
    update_additional_files()
    mock_git.add.assert_called_once_with("somefile.txt")


def test_update_additional_files_one_in_config_two_changes(mock_git, mocker):
    """
    Given two file changes, but only one referenced in the config, we
    expect that single file to be added.
    """
    mocker.patch(
        "semantic_release.vcs_helpers.config.get",
        wrapped_config_get(**{"include_additional_files": "anotherfile.txt"}),
    )
    mocker.patch(
        "semantic_release.vcs_helpers.get_changed_files",
        return_value=["somefile.txt", "anotherfile.txt"],
    )
    update_additional_files()
    mock_git.add.assert_called_once_with("anotherfile.txt")


def test_update_additional_files_two_in_config_one_change(mock_git, mocker):
    """
    Given two file changes, but only one referenced in the config, we
    expect that single file to be added.
    """
    mocker.patch(
        "semantic_release.vcs_helpers.config.get",
        wrapped_config_get(
            **{"include_additional_files": "somefile.txt,anotherfile.txt"}
        ),
    )
    mocker.patch(
        "semantic_release.vcs_helpers.get_changed_files",
        return_value=["anotherfile.txt"],
    )
    update_additional_files()
    mock_git.add.assert_called_once_with("anotherfile.txt")
