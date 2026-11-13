# Terminal compatibility checklist (SRD 2.3, M5)

## Already automated

`tests/test_terminals.py` runs the real client in a pseudo-terminal at 80x24 with
`TERM=xterm-256color` (truecolor and 256 colors), `TERM=xterm` and `TERM=linux`
(16 colors), and `NO_COLOR=1`. It also checks that 16-color mode never sends
extended color codes and that a 79-column terminal shows the "enlarge" notice.
The client tests also check every rendered screen for characters outside ASCII
and box drawing (SRD 9.1).

## Still to do by hand

These things can only be checked in the real terminals: fonts, how each terminal
sends keys and pastes, and shortcuts that the OS or the terminal intercepts.

Run each check at exactly 80x24, then at a normal size. Mark each cell **ok**, or
note what went wrong.

| Check | Warp | Windows Terminal | iTerm2 | macOS Terminal | GNOME Terminal | Konsole | Alacritty | kitty | WezTerm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1. Board renders; box lines join up | | | | | | | | | |
| 2. Green accent and gray text readable | | | | | | | | | |
| 3. `NO_COLOR=1 gcboard` readable | | | | | | | | | |
| 4. Keys `l t p m ? r w q` all work | | | | | | | | | |
| 5. Today: `s`, confirm, timer ticks | | | | | | | | | |
| 6. Today: `o` opens the browser | | | | | | | | | |
| 7. Writeup: `tab` / `shift+tab` switch answers | | | | | | | | | |
| 8. Writeup: `ctrl+n` / `ctrl+p` scroll the code | | | | | | | | | |
| 9. Writeup: `ctrl+s` submits (not frozen by XON/XOFF) | | | | | | | | | |
| 10. Writeup: `ctrl+q` and `q` do nothing | | | | | | | | | |
| 11. Writeup: terminal paste shortcut* | | | | | | | | | |
| 12. Writeup: right-click / middle-click paste* | | | | | | | | | |
| 13. Writeup: close the window, reopen, resumes | | | | | | | | | |
| 14. Resize below 80x24 shows the notice; `q` quits | | | | | | | | | |

\* Record which of these happens:

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
