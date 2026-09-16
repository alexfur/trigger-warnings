# Self-hosted CI runner

CI for this repository runs on a self-hosted macOS runner. GitHub-hosted macOS runners
stopped starting jobs on 2026-09-13 with a billing error, so an Apple Silicon Mac you
control does the work instead. This page registers that Mac, keeps the runner alive across
reboots, and checks that it picks up jobs.

## Checklist

- [ ] Prerequisites: Apple Silicon Mac, Xcode or Command Line Tools, Homebrew, admin
      rights on the repository, a local checkout of this repository.
- [ ] Get a registration token from Settings, Actions, Runners, New self-hosted runner.
- [ ] Run `scripts/setup-runner.sh <TOKEN>` within an hour of getting the token.
- [ ] Confirm the launchd service is installed: `./svc.sh status` in
      `~/actions-runner-trigger-warnings` prints "Started".
- [ ] Confirm GitHub shows the runner green and "Idle", and the queued CI run goes green.
- [ ] Read the caveats once: PATH under launchd, logged-in session, sleep, folder location.

## Prerequisites

- An Apple Silicon Mac. The setup script refuses Intel Macs.
- Xcode or the Command Line Tools installed and selected: `xcode-select -p` prints a
  developer directory.
- Homebrew installed at `/opt/homebrew`.
- Admin access to `alexfur/trigger-warnings`. Registration tokens need it.
- A local checkout of this repository, for `scripts/setup-runner.sh`.

## 1. Get a registration token

1. Open the repository's runner settings:
   https://github.com/alexfur/trigger-warnings/settings/actions/runners
2. Click **New self-hosted runner**, then choose **macOS** and **ARM64**. Direct link:
   https://github.com/alexfur/trigger-warnings/settings/actions/runners/new?arch=arm64&os=mac
3. In the **Configure** block, copy the value after `--token`. It is a 29-character
   string that starts with `A`.

The token is single use and expires after one hour. It can register one runner and
nothing else, but treat it as a secret anyway: do not commit it, and do not paste it into
a canvas note or a chat.

Command-line alternative, for a `gh` login with admin rights on the repository:

```bash
TOKEN="$(gh api -X POST repos/alexfur/trigger-warnings/actions/runners/registration-token -q .token)"
```

## 2. Register the runner and install the service

From the repository checkout, run one command:

```bash
scripts/setup-runner.sh "$TOKEN"
```

Paste the token in place of `"$TOKEN"` if you copied it from the browser. The script,
in order:

1. Refuses to run on anything but macOS on Apple Silicon.
2. Creates `~/actions-runner-trigger-warnings` (override with `RUNNER_DIR=/other/path`)
   and, on first run, downloads and unpacks runner 2.337.0 for macOS arm64 into it.
3. Writes `.env` in that directory with a PATH that starts with `/opt/homebrew/bin`, so
   jobs started by launchd find `node`, `npm` and other tools.
4. Runs `./config.sh --url https://github.com/alexfur/trigger-warnings --token <TOKEN>
   --labels self-hosted,macOS,ARM64 --unattended --replace`. The runner takes the Mac's
   hostname as its name; `--replace` re-registers over a GitHub entry with that name.
5. Runs `./svc.sh install` and `./svc.sh start`.

It ends with `Runner installed and started in /Users/<you>/actions-runner-trigger-warnings`.
Running it again is safe: it skips the registration when `.runner` already exists and the
service install when the plist already exists. On such a second run `svc.sh start` prints
`Load failed: 5: Input/output error` because the service is already loaded; the line is
harmless and `status` still reports Started.

## 3. How the launchd service keeps it running

`./svc.sh install` writes a LaunchAgent plist to
`~/Library/LaunchAgents/actions.runner.alexfur-trigger-warnings.<runner name>.plist`
(spaces in the name become underscores) and points its logs at
`~/Library/Logs/actions.runner.alexfur-trigger-warnings.<runner name>/stdout.log` and
`stderr.log`. `./svc.sh start` loads that plist with `launchctl load -w`, which also marks
it to start at every login (RunAtLoad). Because it is a LaunchAgent, the runner runs as
your user and only inside a logged-in session: after a reboot it comes back when you log
in, not before.

Service commands, run from the runner directory:

| Command | Effect |
|---|---|
| `./svc.sh status` | Prints the plist path, then "Started" with the PID or "Stopped" |
| `./svc.sh stop` | Unloads the agent; GitHub shows the runner as Offline |
| `./svc.sh start` | Loads the agent again |
| `./svc.sh uninstall` | Stops the agent and deletes the plist; the GitHub registration stays |

Never run `svc.sh` with `sudo`. It refuses, and a root-owned plist would break the
per-user service.

## 4. Verify

1. Open https://github.com/alexfur/trigger-warnings/settings/actions/runners. The runner is
   listed with a green dot and the status **Idle**. It reads **Active** while a job runs
   and grey **Offline** when the service is down.
2. From a terminal:

   ```bash
   gh api repos/alexfur/trigger-warnings/actions/runners -q '.runners[] | "\(.name) \(.status) busy=\(.busy)"'
   ```

   prints `<runner name> online busy=false`.
3. Locally, `./svc.sh status` in `~/actions-runner-trigger-warnings` reports "Started".

## Caveats

- **Xcode.** After an Xcode update, run `sudo xcodebuild -license accept` once, or
  the first job hangs on the licence prompt.
- **PATH under launchd.** launchd does not read your shell profile, so `/opt/homebrew/bin`
  is not on the PATH unless something adds it. Three things do: the `.path` file that
  `config.sh` writes with the PATH of the shell it ran from, which the service applies
  first; the `.env` file the setup script writes, which the runner applies on top at
  start-up; and the workflow itself, which can prefix `/opt/homebrew/bin:/usr/local/bin`.
  The runner takes each `.env` value verbatim, so write `PATH=/opt/homebrew/bin:...` with
  no quotes and no `export`. Run the setup from a normal shell where `node` resolves.
- **Where the runner lives.** Keep `RUNNER_DIR` outside `~/Documents`, `~/Desktop` and
  `~/Downloads`. macOS privacy protection (TCC) blocks launchd processes from those
  folders. The default `~/actions-runner-trigger-warnings` is fine.
- **Logged-in session and sleep.** The service runs only while your user is logged in,
  and a sleeping Mac runs nothing. For a Mac that should take jobs unattended, enable
  automatic login and stop it sleeping (System Settings, Energy, or
  `sudo pmset -a sleep 0`). A locked screen is fine.
- **One runner, one job at a time.** The checkout lives in
  `~/actions-runner-trigger-warnings/_work/trigger-warnings/trigger-warnings`.
- **Private repository only.** Keep self-hosted runners off public repositories: a pull
  request from any fork could run code on this Mac. `alexfur/trigger-warnings` is private.
- **Labels.** The workflow should use `runs-on: [self-hosted, macOS]`. The runner
  registers with `self-hosted`, `macOS` and `ARM64`, so it matches.
- **Updates.** The runner updates itself when GitHub releases a new version. 2.337.0 is
  only the version installed first.

## Remove or re-register

`config.sh` refuses to reconfigure a directory that is already registered, so to move the
runner, rename it, or register it afresh with a new token, remove it first:

```bash
cd ~/actions-runner-trigger-warnings
./svc.sh stop && ./svc.sh uninstall
./config.sh remove --token "$(gh api -X POST repos/alexfur/trigger-warnings/actions/runners/remove-token -q .token)"
```

The removal token also comes from the runner's page under Settings, Actions, Runners
(open the runner, then **Remove**). Afterwards, run `scripts/setup-runner.sh` again with a
fresh registration token.
