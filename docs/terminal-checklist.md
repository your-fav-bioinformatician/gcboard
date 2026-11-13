# Terminal compatibility checklist (SRD 2.3, M5)

## Already automated

`tests/test_terminals.py` runs the real client in a pseudo-terminal at 80x24 with
`TERM=xterm-256color` (truecolor and 256 colors), `TERM=xterm` and `TERM=linux`
(16 colors), and `NO_COLOR=1`. It also checks that 16-color mode never sends
extended color codes and that a 79-column terminal shows the "enlarge" notice.
The client tests also check every rendered screen for characters outside ASCII
and box drawing (SRD 9.1).

## Setup: a demo server

You don't need real Codeforces or GitHub accounts. From the repository root, run:

```bash
uv run --extra server python scripts/demo_server.py --data /path/to/empty-folder
```

It prints the command for the terminal under test. That command logs you in as
`demo`, with three other members on the board, an open daily task you haven't
started, and an open project with a verified repo, so `s` on the Project screen
starts a real writeup. For check 5, `curl -X POST http://127.0.0.1:8765/demo/ac`
records an accepted solution. The demo folder is wiped and reseeded each time the
server starts.

## Still to do by hand

These things can only be checked in the real terminals: fonts, how each terminal
sends keys and pastes, and shortcuts that the OS or the terminal intercepts.

Run each check at exactly 80x24, then at a normal size. Mark each cell **ok**, or
note what went wrong.

| Check | Warp | Windows Terminal | iTerm2 | macOS Terminal | GNOME Terminal | Konsole | Alacritty | kitty | WezTerm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1. Board renders; box lines join up | ok | | | | | | | | |
| 2. Green accent and gray text readable | ok | | | | | | | | |
| 3. `NO_COLOR=1 gcboard` readable | ok | | | | | | | | |
| 4. Keys `l t p m ? r w q` all work | ok | | | | | | | | |
| 5. Today: `s`, confirm, timer ticks | ok | | | | | | | | |
| 6. Today: `o` opens the browser | ok* | | | | | | | | |
| 7. Writeup: `tab` / `shift+tab` switch answers | ok | | | | | | | | |
| 8. Writeup: `ctrl+n` / `ctrl+p` scroll the code | ok | | | | | | | | |
| 9. Writeup: `ctrl+s` submits (not frozen by XON/XOFF) | ok | | | | | | | | |
| 10. Writeup: `ctrl+q` and `q` do nothing | ok | | | | | | | | |
| 11. Writeup: terminal paste shortcut* | ok | | | | | | | | |
| 12. Writeup: right-click / middle-click paste* | ok | | | | | | | | |
| 13. Writeup: close the window, reopen, resumes | ok | | | | | | | | |
| 14. Resize below 80x24 shows the notice; `q` quits | ok | | | | | | | | |

Warp: checked on Ubuntu (GNOME), Warp 0.2026.10.21. *Check 6 first opened a
browser other than the default (Brave). Python's `webbrowser` treats `xdg-open`
as failed when it exits at once, then tries further browsers on its list. It's fixed by opening links with `xdg-open` / `open` / the Windows
handler directly, and needs re-checking on the next pass.

\* For checks 11 and 12, record which of these happens:

- **blocked**: "Pasting is disabled" appears. The terminal sent a bracketed paste,
  and the editor refused it.
- **rolled back**: the text appears, then disappears at the next autosave with the
  "faster than anyone can type" warning. The terminal sent the paste as keystrokes,
  and the server's rate check caught it.
- **kept**: the pasted text stays after an autosave. This is a bug: report it with
  the terminal's name and version.

Pastes of 50 characters or fewer can slip through as "kept". That is the burst
allowance in `[writeup] burst_allowance_chars`, and it's expected.

## Known platform notes

- **macOS** takes `ctrl+up` / `ctrl+down` for Mission Control. That's why the
  writeup uses `ctrl+n` / `ctrl+p` to scroll the code.
- **Windows**: run the client in Windows Terminal. The old console host is not on
  the supported list.
- **Warp**'s input editor may capture some keys before the app sees them. If
  writeup keys fail there, try with Warp's own input mode turned off for the session.
