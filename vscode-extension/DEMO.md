# Demo: Command Gate with fake data

How to show Command Gate without the real backend: the line under Claude Code's
permission prompt ([Claude Code plugin](../cmdgate-plugin/README.md)) and the card in
VS Code ([add-on](README.md)). Everything it displays in this demo comes
from [`demo/mock-app.js`](demo/mock-app.js): fake, keyword-based answers. Fine for showing
how it works; never report them as model results.

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

## 4. A real Claude Code permission prompt

1. Open a terminal in the demo folder and start Claude Code with the plugin loaded from
   your checkout, **in default mode** (replace `<repo>` with where you cloned this repository):
   ```
   cd ~/cmdgate-demo
   claude --plugin-dir <repo>/cmdgate-plugin --permission-mode default
   ```
   Default mode matters: in auto mode or bypass mode Claude Code never shows a permission
   prompt, so there is nothing to give a second opinion on. (If the plugin is installed
   from the marketplace, `cd ~/cmdgate-demo` and `claude --permission-mode default` is enough.)
2. Paste this prompt:
   > I'm testing a permission-prompt add-on. Run these shell commands one at a time, each as its own tool call, and don't add any other commands:
   > 1. `mkdir -p /tmp/cmdgate-demo`
   > 2. `rm -rf /tmp/cmdgate-demo`
   > 3. `git push --force origin main`
   > 4. `sudo rm -rf /var/log/cmdgate-demo`
3. At each permission prompt, read the line under the dialog in the terminal:
   `Command Gate: UNSAFE · risk high 69% · dangerous yes 86% · root no 5%` (fake numbers).
   If the dev-host window from step 2 is open, the same answer also opens as a card there.
   The fake app's terminal prints one line per check.
   - Commands 1 and 2 are harmless: approve them if you like.
   - **Press "No" on 3 and 4.** They are only there to show the warning.

## 5. Clean up

1. Exit Claude Code; nothing was installed, so nothing needs removing.
2. Stop the fake app with **Ctrl+C**.
3. Optional: delete `~/cmdgate-demo`.

## If something doesn't show

- **"Gate: app offline" / "No second opinion":** the fake app isn't running, or isn't on
  port 8009.
- **No line under a Claude prompt:** make sure Claude actually showed a prompt (default mode,
  and the command isn't already allowed) and that it was started with `--plugin-dir cmdgate-plugin`.
  `claude --debug` logs why a plugin hook was skipped.
- **Line shows but no card in VS Code:** the dev-host window must be open, and the plugin's
  `forwardToIde` setting on (default). **Output → Command Gate** in that window logs every check.
- **F5 shows a debugger list (Firefox, Node, Python…):** VS Code has a different folder open.
  Open the repo root itself, or use Run and Debug → *Run Command Gate add-on*.
