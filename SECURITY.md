# Security policy

## Credentials

`trigger-warnings` accepts API credentials from environment variables first. It
can store them in the operating system keychain only when the user explicitly
runs the terminal setup flow. An exported variable overrides a stored value.

The project never reads or writes a credential file. It has no command-line
option for an OpenSubtitles username or password. The setup prompts hide secret
values, and the tool never echoes credentials in normal output, errors, JSON or
command lines.

Do not paste a credential into an issue, pull request, command, log or chat.
Revoke a credential exposed by mistake, then create a replacement with its
provider.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Email the repository
owner through the contact address on the GitHub profile with a description,
reproduction steps and the affected version. Remove credentials, tokens and
private media from the report.

The project will acknowledge the report, investigate it privately and agree on
a disclosure plan before publishing a fix.
