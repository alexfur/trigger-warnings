# Contributing

## Development setup

Use Python 3.9 or newer. The project has no runtime dependencies.

```sh
python3 -m unittest discover -s tests -v
```

`RUN_MEDIA_TESTS=1` enables the FFmpeg integration checks. Run the normal suite
before sending a pull request. The continuous-integration matrix covers Python
3.9 through 3.13 on Ubuntu and macOS, then builds and checks the distribution.

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
