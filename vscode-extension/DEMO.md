# Demo: Command Gate add-on with fake data

How to show the add-on without the real backend. Everything it displays in this demo comes
from [`demo/mock-app.js`](demo/mock-app.js): fake, keyword-based answers. Fine for showing
the add-on; never report them as model results.

## 1. Start the fake app (leave this terminal open)

From the repo root:

```
node vscode-extension/demo/mock-app.js
```

It prints `DEMO mock app (fake data) on http://127.0.0.1:8009/v1/systemone`, then one line
per command it is asked about. That line is the quickest way to see that a check happened.

## 2. Start the add-on

1. Create an empty demo folder in your home folder, once:
   `mkdir ~/cmdgate-demo` (PowerShell: `mkdir $HOME\cmdgate-demo`).
   It sits outside the repo and outside any git repo, so the risky-looking test commands
   below have nothing to act on.
2. Open the repo in VS Code and press **F5**, or open Run and Debug (Ctrl+Shift+D), pick
   **Run Command Gate add-on** and press ▶.
3. A window titled **[Extension Development Host]** opens with `~/cmdgate-demo`.
4. Look for **🛡 Gate** in its status bar, bottom left. Other extensions are switched off in
   this window on purpose: on some setups another extension crashes the development window.

## 3. Check a command by hand (optional)

**Ctrl+Shift+P**, then **Command Gate: Check command…**, then `rm -rf ~/`. The
second-opinion card opens beside the editor: "Look closely before you approve", risk level
critical. Then try `ls -la` ("Looks safe to run"); the first check moves to *Earlier checks*.

## 4. Hook up Claude Code permission prompts

1. In the dev-host window: **Ctrl+Shift+P**, **Command Gate: Install Claude Code hook**,
   then **This project only (cmdgate-demo)**.
2. Check that `~/cmdgate-demo/.claude/settings.local.json` now exists with a
   `PermissionRequest` entry. It shows in the dev-host window's Explorer.
3. Open a normal terminal and start Claude Code **in the demo folder, in default mode**:
   ```
   cd ~/cmdgate-demo
   claude --permission-mode default
   ```
   Default mode matters: in auto mode or bypass mode Claude Code never shows a permission
   prompt, so there is nothing for the add-on to check.
4. Paste this prompt:
   > I'm testing a permission-prompt add-on. Run these shell commands one at a time, each as its own tool call, and don't add any other commands:
   > 1. `mkdir -p /tmp/cmdgate-demo`
   > 2. `rm -rf /tmp/cmdgate-demo`
   > 3. `git push --force origin main`
   > 4. `sudo rm -rf /var/log/cmdgate-demo`
5. Each time Claude shows a permission prompt, look at the dev-host window. The
   second-opinion card opens beside the editor with the command, Claude's description of it,
   the verdict, the risk scale and the dangerous/root facts; the fake app's terminal prints a
   line.
   - Commands 1 and 2 are harmless: approve them if you like.
   - **Press "No" on 3 and 4.** They are only there to show the warning card.

## 5. Clean up

1. In the dev-host window: **Command Gate: Remove Claude Code hook**, then
   **This project only (cmdgate-demo)**.
2. Stop the fake app with **Ctrl+C**.
3. Optional: delete `~/cmdgate-demo`.

## If something doesn't show

- **"Gate: app offline" / "No second opinion":** the fake app isn't running, or isn't on
  port 8009.
- **No card at a Claude prompt:** make sure Claude actually showed a prompt (default mode,
  and the command isn't already allowed), and that the session was started after the hook was
  installed. **Output → Command Gate** in the dev-host window logs every check.
- **F5 shows a debugger list (Firefox, Node, Python…):** VS Code has a different folder open.
  Open the repo root itself, or use Run and Debug → *Run Command Gate add-on*.
