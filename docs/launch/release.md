# Release

How the package reaches PyPI, and which parts only the repository owner can
do. Nothing in this file has been performed. No package has been submitted to
PyPI or TestPyPI.

## What is already true

| Fact | Evidence |
| --- | --- |
| The build works | `python -m build` produces `trigger_warnings-0.3.0.tar.gz` and `trigger_warnings-0.3.0-py3-none-any.whl`. |
| The metadata is valid | `python -m twine check dist/*` reports `PASSED` for both. |
| The wheel installs and runs | A new virtual environment plus `pip install dist/*.whl` gives a working `trigger-warnings` that completes the no-account command. |
| The sdist is complete | It carries the package, `examples/`, `assets/`, `scripts/`, `skills/`, `tests/` and every top-level document. |
| The name looks free | `https://pypi.org/pypi/trigger-warnings/json` answers `404`, so no project of that name is registered. The owner still confirms this at publish time. |
| Publishing is least privilege | `.github/workflows/release.yml` grants no permission by default. The publish job takes `contents: read` and `id-token: write` and nothing else, and the checkout does not persist credentials. |
| Only a version tag publishes | The workflow triggers on `v*.*.*`. A tag without two dots is ignored. |

## Version and tag convention

The tag is `v` followed by the version in `pyproject.toml`, for example
`v0.3.0`. The two must match. The workflow does not check that, so the owner
checks it before tagging.

The current source version is `0.3.0` and has deliberately not been changed.
If the first public release should carry a different number, bump
`pyproject.toml` and `CHANGELOG.md` in a separate reviewed change first.

## Owner steps, in order

Every step here needs the repository owner. An implementation agent must not
perform any of them.

1. **Confirm the PyPI name.** Sign in to PyPI and check that
   `trigger-warnings` is available to you.
2. **Configure trusted publishing.** On PyPI, add a pending publisher for the
   project:
   - Owner: `alexfur`
   - Repository: `trigger-warnings`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. **Create the GitHub environment.** In repository settings, add an
   environment named `pypi`. Add a required reviewer if you want a manual gate
   before any publish.
4. **Make the repository public.** The release workflow works either way, but
   the launch needs it.
5. **Confirm the version.** `pyproject.toml` and the `CHANGELOG.md` heading
   agree, and the working tree is clean.
6. **Move the changelog entry.** Rename `## Unreleased` to the version and
   date being released.
7. **Tag and push.**
   ```sh
   git tag -a v0.3.0 -m "0.3.0"
   git push origin v0.3.0
   ```
8. **Watch the workflow.** The Actions tab shows the `Publish to PyPI` run. It
   builds, runs `twine check`, then publishes through OpenID Connect. No PyPI
   token is stored anywhere.
9. **Create the GitHub release.** Point it at the tag and paste the changelog
   section.
10. **Confirm the result.** Visit `https://pypi.org/project/trigger-warnings/`,
    then in a clean environment:
    ```sh
    pipx install trigger-warnings
    trigger-warnings --version
    ```

## After the release

Update the README's `After the first release` note once the PyPI page exists,
so the install command is no longer labelled as unavailable.

## Supported installation commands

| Audience | Command | Notes |
| --- | --- | --- |
| Someone who wants the command | `pipx install trigger-warnings` | Only after the first release. Gives an isolated install and the `trigger-warnings` command on PATH. |
| Someone without pipx | `python3 -m pip install --user trigger-warnings` | Same package. Also only after the first release. |
| A contributor, or anyone who wants the example files | `git clone` then `pip install .` in a virtual environment | This is the README's primary path, and the only one that includes `examples/`. |

The bundled example fixtures ship in the sdist but not in the wheel, so a
`pipx` install gives the command without `examples/dialogue.srt`. That is why
the README leads with the source install rather than PyPI, and it is not worth
moving the fixtures into the package to change.

## Known limitations to accept or fix

| Limitation | Effect | Options |
| --- | --- | --- |
| The README uses relative image paths. | GitHub renders them. The PyPI long description will not, so the project page shows broken images. | Accept it, or after the repository is public switch the four `<img src>` values to `https://raw.githubusercontent.com/alexfur/trigger-warnings/main/assets/...`. That is an owner decision because it only works once the repository is public. |
| `pypa/gh-action-pypi-publish@release/v1` is a moving ref. | The action can change under the workflow. | It is the ref PyPA documents and supports. Pinning to a commit SHA is stricter but has to be reviewed and updated by hand. |
| `Development Status :: 3 - Alpha`. | Sets expectations low on the PyPI page. | Accurate today. Revisit after the first round of external reports. |

## If a publish fails

- **`Trusted publishing exchange failure`**: the pending publisher on PyPI does
  not match the repository, workflow filename or environment. Fix it on PyPI
  and re-run the job. Do not add an API token as a workaround.
- **The version already exists**: PyPI never accepts a re-upload of the same
  version. Bump the version, tag again, and publish that.
- **`twine check` fails in the workflow**: the metadata or README changed since
  the last local check. Reproduce with `python -m build && python -m twine
  check dist/*` before tagging again.
