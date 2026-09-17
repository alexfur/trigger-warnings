# Release

The next candidate is `0.4.0a2`. PyPI and TestPyPI contain `0.4.0a1`.
Staging `0.4.0a2` to TestPyPI and promoting to PyPI provides the packaged
release with cloud and local model support installed via `pip`.

## One-time account setup

On both [PyPI](https://pypi.org/manage/account/publishing/) and
[TestPyPI](https://test.pypi.org/manage/account/publishing/), the account owner
adds a pending GitHub publisher:

| Setting | Value |
| --- | --- |
| Project | `trigger-warnings` |
| Owner | `alexfur` |
| Repository | `trigger-warnings` |
| Workflow | `release.yml` |
| Environment | `pypi` for PyPI; `testpypi` for TestPyPI |

The matching GitHub environments must exist. Publishing uses short-lived
OpenID Connect credentials; no permanent PyPI API token is needed.

A private repository can publish a package. Its distributed source becomes
public, while GitHub links remain inaccessible to non-collaborators until
the repository owner makes the repository public separately.

## Prepare and stage

1. Update `pyproject.toml`, `trigger_warnings/__init__.py` and
   `CHANGELOG.md` together.
2. Run the unit tests, build the sdist and wheel, and check their metadata:

   ```bash
   python -m unittest discover -s tests -v
   python -m pip install build twine
   python -m build
   python -m twine check dist/*
   ```

3. Install the wheel in a clean environment outside the checkout. Check the
   CLI with the synthetic examples. On Apple Silicon, install the wheel's
   `[vision]` extra and run `scripts/smoke_vision.py` with that environment's
   Python. This test downloads the default model if necessary.
4. Commit and push with the repository's approved Git workflow. Create a
   version tag matching both declarations, such as `v0.4.0a2`.

A matching tag starts `release.yml`. The workflow first runs the reusable
test workflow: unit tests on Ubuntu and macOS, a clean base-wheel install,
and the optional vision dependency install on an Apple Silicon runner.
The hosted vision check tests imports and synthetic provider behaviour;
real model inference is checked separately on a local Mac.

Only after those jobs pass does publishing run. It checks that the tag matches
both version declarations, builds the distributions, runs `twine check`,
tests the built wheel outside the checkout, and uploads to TestPyPI.

## Verify and promote

Install the staged alpha in a fresh environment:

```bash
python -m pip install --index-url https://test.pypi.org/simple/ \
  --no-deps 'trigger-warnings==0.4.0a2'
trigger-warnings --version
```

The base package has no runtime dependencies, so `--no-deps` isolates this
check from TestPyPI's unrelated packages. Test model dependencies against
regular PyPI separately.

After verification, dispatch the release workflow on the same tag:

```bash
gh workflow run release.yml --ref v0.4.0a2 -f target=pypi
```

The production run repeats the required checks, downloads the staged sdist
and wheel from TestPyPI, verifies their published SHA-256 hashes and checks
the wheel again. It uploads those same distributions to PyPI without
rebuilding them.

Verify the production install before announcing the release:

```bash
python -m pip install 'trigger-warnings==0.4.0a2'
trigger-warnings --version
```

For local scanning on Apple Silicon, install
`trigger-warnings[vision]==0.4.0a2`. Update the README's install instructions
only after this succeeds.

## Failed releases

- If every job fails before any step runs, read the check annotations. The
  September 2026 runs were blocked by GitHub account billing or spending
  limits, including the TestPyPI publish job. Resolve the account restriction
  before retrying; this error does not diagnose PyPI credentials.

- A Trusted Publishing error usually means an account-side publisher is absent
  or does not match the repository, workflow or environment. Correct the
  publisher and rerun; do not paste a token into the workflow.
- A branch dispatch fails the tag check. Select a version tag.
- A tag/version mismatch requires correcting the version or tag before publishing.
- PyPI does not allow replacing a published file. If code changes after
  staging, increment the version and use a new tag.
- An inference failure must be investigated before promoting a model release.
  Passing the base suite does not establish model-detection accuracy.
