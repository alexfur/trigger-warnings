# Contributing

## Development setup

Use Python 3.9 or newer. The project has no runtime dependencies.

```sh
python3 -m unittest discover -s tests -v
```

`RUN_MEDIA_TESTS=1` enables the FFmpeg integration checks. Run the normal suite
before sending a pull request. Continuous integration runs on GitHub-hosted
macOS runners with Python 3.12: unit tests, a build and wheel check, and the
`vision` and `gemini` extras. The first pull request from a new contributor
waits for a maintainer to approve the workflow run.

## Changes

Keep each change narrow and add a synthetic test for a behaviour change. Do not
commit commercial subtitle files, copied timelines, account credentials or
personal trigger profiles. Fixtures must be synthetic or appropriately
licensed.

Never put a credential in an argument, fixture, test name or expected output.
The tool deliberately accepts sensitive values through the environment or its
hidden terminal prompt only.

## Releases

Maintainers publish a version tag after the package job passes. The release
workflow uses PyPI Trusted Publishing through GitHub Actions OpenID Connect, so
the repository does not store a PyPI token.
