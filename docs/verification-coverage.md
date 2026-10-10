# PLAN section 10 coverage matrix

This table maps every row of the [PLAN section 10 verification matrix](../PLAN.md#10-verification-matrix) and each scenario phrase in it to the **real-Herdr sandbox scenarios** that verify it (primary evidence) and to the unit tests that cover the same logic (secondary evidence only). Sandbox scenarios run the real Herdr 0.9.3 server and client in the bubblewrap harness (`tools/verify_sandbox.py --run --suite next`, definitions in `tools/next_suite.py` and `tools/next_faults.py`); results are in [sandbox-next/actual-results.json](sandbox-next/actual-results.json), see [verification-results.md](verification-results.md). The few items that cannot be exercised against a real Herdr are listed at the end with the exact reason.

How faults are injected (sandbox only): the plugin's Python processes call a test-only CLI shim (`tools/herdr_shim.py`, see [verification.md](verification.md#next-suite-current-revision-plan-section-10-scenarios)) instead of the `herdr` binary. Per scenario the harness arms rules in the shim directory (`faults.json`); `Lab.reset` removes them. A rule can make a call fail like a lost connection, print malformed JSON or an error envelope, rewrite the JSON (unknown envelope type, missing list, a schema without a method, a padded `process-info`), run the real write and then lose its response (exit non-zero or hang past the client's 5 s timeout), report another version string, tamper with the private handoff file just before the popup starts, or make `plugin pane open` fail. The real server never misbehaves; every server-side effect is read back through the real CLI. Scenarios that change targets (close, move, kill, restart a pane or agent) do so with the real Herdr CLI and real processes.

Scenario series: S sync, R rename, T theme/parity, L lifecycle, H harness self-test (older series, see verification.md); **F** transport/API fault injection, **X** target changes, handoff, context and terminal modes, **E** editor inside the real popup (new in this revision).

## Editor

| Required scenario | Sandbox scenarios | Unit tests (secondary) |
| --- | --- | --- |
| Existing name | R01 (name shown, cursor at the end), E01 | `test_editor.py::test_*`, `test_ui_rename.py::test_run_dialog_selects_rename_dialog` |
| Absent name | E06 (empty input, hint, empty Enter is a zero-write close, typed name sets it) | `test_controller.py::test_clear_of_an_already_unnamed_agent_is_a_zero_write_close`, `test_unnamed_live_agent_can_be_renamed` |
| Replace-on-type (Herdr's flag exists in the model; the agent dialog opens without it) | E01 (typing appends to the opened name) | `test_ui_rename.py::test_open_has_no_replace_on_type`, `test_editor.py::test_replace_on_type_*` |
| Character / word / grapheme movement | E01 (Left/Right/Home/End/Ctrl+A/E/F, Alt+B/F against the editor model after every key), E03 (combining marks, CJK, emoji with skin-tone modifier) | `test_editor.py::test_movement_over_clusters`, `test_word_classes_backward_and_forward`, `test_unicode_graphemes_and_boundary_changing_edits` |
| All kill / yank keys | E02 (Ctrl+W/K/U/Y/D, Alt+D, Alt+Backspace, Delete, Backspace, Ctrl+H compared with the model after every key) | `test_editor.py::test_every_binding_edits_at_the_cursor`, `test_kill_word_right_and_kill_buffer_overwrite`, `test_yank_inserts_at_cursor_and_reports_empty` |
| Viewport scrolling | E04 (140-character text, cursor movement, Home/End, viewport compared with the model), R13 | `test_editor.py::test_viewport_*`, `test_ui_rename.py::test_long_text_scrolls_with_cursor_visible` |
| Paste with CR / LF / Tab / control characters (D7) | E05 (CR, LF, CRLF, Tab become spaces, BEL becomes U+FFFD, invalid name is not written), R10 (newline, control) | `test_editor.py::test_normalize_input`, `test_control_characters_stay_visible_for_validation`, `test_ui_rename.py::test_bracketed_paste_uses_editor_insert` |
| Enter save | R01 | `test_controller.py::test_rename_reads_before_and_after_one_write_to_the_frozen_target` |
| Trim | R02 | `test_controller.py::test_rust_whitespace_is_trimmed_before_validation_and_write` |
| Ctrl+C clear-only | R03 | `test_ui_rename.py::test_ctrl_c_only_clears_the_field` |
| Empty save clears | R04 (`--clear`), F08 (with a lost response) | `test_controller.py::test_empty_input_clears_the_name_with_one_write_and_verification` |
| Esc cancel | R05 | `test_ui_rename.py::test_escape_cancels_without_submitting` |
| Mouse save / clear / cancel | R06, R07, R08, R09 (press elsewhere cancels) | `test_ui_rename.py::test_press_on_save_submits`, `test_press_on_clear_clears_and_stays_open`, `test_press_anywhere_else_cancels` |

## Sync selection

| Required scenario | Sandbox scenarios | Unit tests (secondary) |
| --- | --- | --- |
| One confirmation per workspace | S01, S02 (one popup for all tabs), X13, X25 | `test_action.py::test_sync_with_eligible_rows_opens_confirm_mode_with_the_frozen_plan` |
| Default No, zero writes | S10 | `test_ui.py::test_default_no_enter_and_other_cancel_keys_never_run` |
| Tab + Enter | S11 | `test_ui.py::test_tab_right_left_and_shift_tab_select_yes` |
| Left/Right + Enter | S12, S13 | same |
| Y / N | S14, S15; S16 (Esc, Ctrl+C) | `test_ui.py::test_y_and_uppercase_y_start_one_batch` |
| Real mouse Yes / No | S17, S18 (SGR events delivered through the real client to the popup) | `test_ui.py::test_mouse_press_on_yes_runs_once_even_when_repeated`, `test_mouse_press_on_no_cancels_and_row_click_selects` |
| Scrolling keeps the buttons visible | X11 (short client: PageUp/PageDown/arrows, Yes/No visible at both ends, last row reachable, Y applies), S22, S23 | `test_ui.py::test_buttons_totals_and_hints_stay_visible_while_the_table_scrolls` |
| Repeated keys / double clicks cannot repeat a batch | S19 (agent `revision` deltas) | `test_sync.py::test_repeated_run_calls_cannot_start_a_second_batch`, `test_ui.py::test_mouse_press_on_yes_runs_once_even_when_repeated` |

## Sync scope

| Required scenario | Sandbox scenarios | Unit tests (secondary) |
| --- | --- | --- |
| Panes across several tabs, including hidden/unfocused | S01-S05 (tab 2 is never focused) | `test_sync.py::test_rows_follow_tab_number_then_pane_order` |
| Other workspace unchanged | S01 (control agent f), X26, X10 | `test_sync.py::test_other_workspaces_are_excluded_even_if_returned` |
| Focus changes after invocation | S21 (focus moved to b while the popup is open), X26 (workspace 2 focused while a workspace-1 plan is applied; rename of pane a while b is focused) | `test_context.py::test_frozen_handoff_ignores_later_plugin_context_and_preserves_connection` |
| Missing workspace context | X25 (workspace-only context, pane-only context resolved from the pane's canonical record, no/empty/malformed/invalid context refused with a notification, exit status != 0, no popup, zero writes) | `test_action.py::test_sync_without_a_workspace_notifies_and_fails_without_popup`, `test_context.py::test_freeze_workspace_*` |
| New panes excluded | X10 (a pane arrives from another workspace after the preview and is not renamed) | `test_sync.py::test_collect_plan_*` |
| Moved / closed panes skipped | S26 (closed pane), X05 (moved to another tab: skipped, not followed), X06 (agent exits), X07 (agent replaced) | `test_sync.py::test_recheck_before_write_skips_changed_rows_without_writing`, `test_replaced_agent_process_is_detected_by_the_target_recheck` |

## Sync source

| Required scenario | Sandbox scenarios | Unit tests (secondary) |
| --- | --- | --- |
| Mixed valid, unnamed, agent-less, invalid, matching panes | S02 | `test_sync.py::test_real_fixture_classification_order_and_tab_info`, `test_invalid_labels` |
| Unnamed agent with a named pane | S03 | `test_sync.py::test_unnamed_agent_is_eligible_and_matching_is_a_noop_row` |
| No eligible rows: notification | S07 (exactly one `notification show` with the English text, no popup, no write) | `test_action.py::test_sync_with_nothing_to_do_notifies_and_opens_no_popup` |
| Conflict-only: information popup | S06, X09 (4-cycle), F12 (every lookup fails) | `test_action.py::test_sync_with_only_problem_rows_opens_the_popup_in_info_mode`, `test_ui.py::test_info_mode_has_only_close_and_never_writes` |
| Label / name changes while confirming | S26 (label, agent name), X05-X07 | `test_sync.py::test_recheck_before_write_skips_changed_rows_without_writing` |
| Label cleared after opening | S27 | same |

## Batch results

| Required scenario | Sandbox scenarios | Unit tests (secondary) |
| --- | --- | --- |
| Duplicate desired labels | S04 | `test_sync.py::test_duplicate_desired_names_conflict_for_every_involved_row_with_no_winner` |
| Names held inside / outside the workspace | S05 (outside), X08 (inside: a swap) | `test_sync.py::test_name_held_by_an_agent_in_another_workspace_conflicts` |
| Swaps / cycles skipped | X08 (swap skipped, exactly one request for the independent row, no temporary clears), X09 (4-cycle, zero writes) | `test_sync.py::test_swap_is_a_conflict_for_both_rows_even_though_each_holder_is_rescheduled`, `test_chain_where_the_holder_will_be_renamed_still_conflicts` |
| Independent success despite failures | F04 (one definite failure, 3 synced, 4 requests), F14 (two lookup failures, other rows sync), S04, S05, S26 | `test_sync.py::test_definite_server_failure_continues_independent_rows` |
| Connection failure stops the queue | F01 (connection lost before row 2: row failed with "Could not connect to Herdr.", 2 rows not attempted, one request in total, Close works), F02 (write fails in transit and the connection stays down: row uncertain, rest not attempted), F03 (write lost, reads fine: uncertain, stop) | `test_sync.py::test_connection_failure_stops_the_queue_and_reports_the_rest_not_attempted`, `test_transport_failure_of_a_write_stops_the_queue` |
| Uncertain not retried | F02/F03 (second request is never repeated), F05 (write applied, response lost: reconciled by a read, exactly one request per row), F06 (write applied, the call times out after the client's 5 s limit: reconciled, not repeated) | `test_sync.py::test_uncertain_write_that_did_not_apply_is_recorded_and_never_retried`, `test_uncertain_write_that_applied_is_reconciled_by_one_read` |
| Cancel during application | S28 (Stop with the second rename in flight: exactly two requests, rest not attempted), S29 (Stop together with Yes) | `test_sync.py::test_cancel_between_rows_finishes_the_in_flight_row_then_stops`, `test_ui.py::test_cancel_during_run_waits_for_the_in_flight_row` |
| Accurate summary | S20, S26 ("2 synced, 5 skipped"), S28, F01-F06 (counts and reasons read from the popup) | `test_ui.py::test_full_batch_outcomes_and_return_value`, `test_progress_then_done_summary_and_close` |

## Target / API

| Required scenario | Sandbox scenarios | Unit tests (secondary) |
| --- | --- | --- |
| Exited targets | X01 (pane closed between open and Enter), X03 (agent exits), X06 (sync) | `test_controller.py::test_exit_replacement_and_alias_movement_stop_without_writing` |
| Moved targets | X02 (pane moved to another tab; refused, never followed), X05 (sync), X10 | same |
| Replaced targets | X04 (agent killed and restarted in the same pane; refused), X07 (sync: same name, new process; skipped) | `test_controller.py::test_foreground_replacement_is_detected_when_public_process_data_is_available`, `test_sync.py::test_replaced_agent_process_is_detected_by_the_target_recheck` |
| Invalid / unknown envelopes | F10 (malformed pane list), F11 (unknown agent-list type, missing `panes`, wrong type), F12 (malformed pane lookups: information popup), F13 (rename dialog: malformed `pane get`, unknown `process-info` type: English error in the dialog, Enter writes nothing) | `test_client.py::test_malformed_list_responses_are_unsupported`, `test_malformed_process_evidence_is_rejected` |
| Missing capabilities | F15 (no `pane.list`, no `agent.list`, `agent.rename` without nullable name i.e. no `--clear`: sync notification "This Herdr version is not supported.", rename refused with the reason in the plugin log, zero writes), F16 (0.9.2) | `test_client.py::test_version_and_capabilities_both_required`, `test_list_and_clear_capabilities_are_required` |
| Newer Herdr version with matching capabilities | F17 (`herdr 0.9.4 (abc123)` with the real schema: sync and rename work) | `test_client.py::test_newer_version_strings_with_build_info_are_accepted` |
| Read failures | F14 (`pane get` error for one pane, `process-info` unknown type for another: both "lookup failed", the others sync), F12, F13 | `test_sync.py::test_per_pane_unsupported_becomes_a_lookup_row_and_other_rows_survive` |
| Uncertain rename / clear reconciliation | Sync: F05, F06. Rename dialog: F07 (response lost; Enter again reconciles; one request), F08 (the same for `--clear`), F09 (name changed elsewhere before the retry: refused, no second write) | `test_controller.py::test_uncertain_applied_write_reconciles_on_user_retry_without_another_write`, `test_uncertain_clear_reconciles_on_retry_without_a_second_write` |

## Terminal / UI

| Required scenario | Sandbox scenarios | Unit tests (secondary) |
| --- | --- | --- |
| Dracula and default theme | T01, T02 (palette colors), T03, T04 (native vs plugin parity) | `test_theme.py`, `test_ui.py::test_theme_colors_for_each_theme` |
| Compact and small terminals | S22 (60x16), S23 (80x24), S24 (50 columns: resize message), R12 (40x10), X11 (100x24 scrolling) | `test_ui.py::test_too_small_terminal_shows_message_blocks_actions_and_allows_escape` |
| Resize | S25, R13 | `test_ui.py::test_resizing_back_restores_the_dialog`, `test_ui_rename.py::test_resize_round_trip_keeps_text` |
| Visible errors | F13 (rename dialog error text), X16-X23 (refusal text in the sync popup), F12 (lookup failures), F01-F04 (row reasons), X01-X04 (refusal in the rename dialog), E05 (invalid paste keeps the dialog) | `test_ui_rename.py::test_error_line_keeps_text_and_uses_error_color`, `test_ui.py::test_error_mode_shows_the_message_with_close` |
| Normal / error exit restores terminal modes | X27 (rename dialog process in its own PTY against the sandbox server: Esc, Enter success, error screen, injected exception with traceback), X28 (sync dialog process: N, Esc, Y then Close, error screen, injected unexpected error while applying). After each exit the byte stream is parsed: every mouse mode (1000/1002/1003/1005/1006/1015), focus reporting, bracketed paste and the alternate screen enabled by the dialog is disabled again and the cursor is shown | `test_ui_rename.py::test_modes_restored_after_success_cancel_and_exception`, `test_real_stream_enables_and_disables_mouse_reporting`, `test_ui.py::test_modes_restored_on_all_exits` |

## Handoff

| Required scenario | Sandbox scenarios | Unit tests (secondary) |
| --- | --- | --- |
| Inline snapshot | Every popup in the suite whose payload is below 48 KiB (for example R01, S01), and every F-series dialog | `test_large_handoff.py::test_small_handoff_preserves_entire_snapshot_and_creates_no_file` |
| Large multi-pane snapshot | X13 (sync plan of four panes padded to above 48 KiB: the launch argument is a short pointer, the file is 0600 and removed once consumed, Yes applies all four rows), X14 (single rename snapshot above 48 KiB), X24 | `test_large_handoff.py::test_many_pane_plan_uses_one_private_consumed_file_and_keeps_check_fields` |
| 4 MiB bound | X15 (plan and snapshot above 4 MiB: "The original target details are too large to open the dialog.", no popup, zero writes, no file left) | `test_large_handoff.py::test_file_bound_is_sane_and_oversized_plan_is_reported_clearly` |
| Malformed context | X16-X23 (garbage, truncated, wrong-workspace JSON), X25 (missing, empty, malformed or invalid invocation context for a real action process), X27/X28 (malformed handoff string: error screen) | `test_context.py`, `test_action.py::test_sync_dialog_with_a_broken_handoff_reports_an_error_runner` |
| File permissions | X16 (mode changed to 0644: refused, file removed, zero writes) | `test_large_handoff.py::test_plan_file_with_unsafe_mode_is_rejected_and_consumed` |
| Ownership | Not exercisable, see below (every other private-file condition is) | `test_large_handoff.py::test_decode_rejects_file_owned_by_a_different_uid` |
| Link count / path checks | X20 (second hard link), X22 (pointer to another directory), X23 (path traversal in the pointer name): refused, zero writes | `test_decode_rejects_pointer_outside_expected_state_directory` |
| Symlink rejection | X17 (the file is replaced by a symlink to a valid private copy: refused, the link is not followed, its target is untouched) | `test_cleanup_never_unlinks_a_substituted_symlink_or_cross_directory_pointer` |
| Cleanup | X13/X14 (removed after consumption), X16/X18/X19/X20/X21 (removed after a refusal), X24 (`plugin pane open` fails: the private file is removed, no popup), X15 (nothing is created above the bound) | `test_large_handoff.py::test_cleanup_*`, `test_action.py::test_failed_popup_launch_removes_private_large_handoff`, `test_failed_sync_popup_launch_removes_the_large_plan_file` |

## Lifecycle

| Required scenario | Sandbox scenarios | Unit tests (secondary) |
| --- | --- | --- |
| Update of the installed plugin without duplicate bindings | L04 (repeated install of the same checkout), L09 (v0.1.0 installed from a tree, the tree is replaced by the current code, `bin/install` again: same two bindings, same config and recovery record, one registration, new dialogs open), L05 | `test_installation.py` (41 tests) |
| Existing recovery record retained | L04, L08 | `test_installation.py::test_partial_restore_leaves_occupied_array_slot_untouched` |
| Later user edits survive uninstall | L06 | `test_installation.py::test_uninstall_preserves_edits_made_since_install_and_restores_only_legacy` |
| Repeated install / uninstall | L04, L07 | `test_installation.py` |
| Rollback to `v0.1.0` | L05 (refusal over another checkout, then new, v0.1.0, new cycle with identical bindings, config and record) | `test_installation.py` |
| Fresh install and dry runs (not in the matrix row text) | L01, L02, L03 | `test_installation.py` |

## Items not exercisable against a real Herdr

| Item | Reason |
| --- | --- |
| A different owner for the private handoff file | The sandbox user cannot create a file owned by another uid (no CAP_CHOWN under `--cap-drop ALL`, a single-uid user namespace), and `O_NOFOLLOW` plus the directory check keep a pre-existing foreign file out of reach. The mode, link-count, regular-file, symlink, directory, filename and content checks around it are all exercised (X16-X23); the uid comparison is covered by `test_decode_rejects_file_owned_by_a_different_uid`. |
| A real socket-level disconnect (stopping the sandbox server) in the middle of a batch | Herdr owns the popup's PTY, so stopping the server kills the popup that must report the result and destroys the shared fixture session. The shim fails the plugin's CLI calls instead (F01-F03): the plugin only ever sees a non-zero exit with no envelope, which is exactly what a vanished server looks like to a CLI. The server was never stopped in these scenarios. |
| Malformed or unknown envelopes, a schema without a method, a newer version string | The real 0.9.3 server never produces them and no other Herdr binary is available. They are injected by rewriting the real response (F10-F17); F17 shows a newer version string, not a newer server. |
| The notification toast on screen | The headless client draws no toast. The notification text is asserted as the argument of the plugin's `notification show` call (S07, F10-F11, F15, F16, X15, X25). |
| How Herdr's popup emulator treats leaked terminal modes | Popup output goes into Herdr's own emulator and is not visible from outside. The modes are checked in the dialog process's output stream when it runs in its own PTY (X27/X28), including crash paths. An exception inside a prompt_toolkit key handler does not exit the dialog (prompt_toolkit prints the traceback and waits for Enter); that library behavior is not exercised. |
| Atomic identity between the last read and the write | Herdr 0.9.3 has no expected-identity precondition on `agent.rename`; a PID-reuse or same-signature replacement is indistinguishable (documented limitation, [compatibility](compatibility.md#identity-coverage)). |
