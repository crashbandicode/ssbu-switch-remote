"""HOME-menu button sequences with capture-card still checks, shared by the controller backends (DESIGN.md 12-13).

A backend mixes `HomeSequences` in and provides:
  _tap_system(name)   taps 'HOME' / 'A' / 'X' (only these sequences may press HOME)
  _capd(method, p)    the capture backend (SwitchClient's)
  scene_status()      the scene receiver's view ({'gate': ...}); used only when `require_quiet` is set
  settle_s, still_dir, homegate_refs

  launch     [no scene report for 5 s, when require_quiet] HOME -> still: SSBU is the selected HOME first tile
             (homegate) -> [still quiet] -> A
  terminate  HOME -> still gate -> X -> still must differ (the close dialog) -> A -> [wait for reports to stop]
  dismiss    still: NOT the HOME first tile (there, A would launch) -> A -> still
"""
import json
import time
from pathlib import Path

from switch_remote import SwitchError

LAUNCH_QUIET_MS = 5000
TERMINATE_WAIT_S = 15.0


class HomeSequences:
    require_quiet = True     # the autopilot route keeps the launch condition of the LAUNCH re-ruling

    def _still(self, label):
        self.still_dir.mkdir(parents=True, exist_ok=True)
        path = Path(self.still_dir) / f'{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}-{label}.png'
        self._capd('ui.screenshot', {'path': str(path), 'width': 1280})
        import homegate
        verdict = homegate.check(path, **({'refs': self.homegate_refs} if self.homegate_refs else {}))
        return str(path), verdict

    def _game_quiet(self):
        """True when no scene report has arrived for LAUNCH_QUIET_MS (the game is not running)."""
        st = self.scene_status()
        g = st.get('gate')
        if g == 'no_scene_report':
            return True, st
        if g == 'no_scene_receiver' or g is None:
            return False, st  # can't tell: fail closed
        return st.get('age_ms', 0) >= LAUNCH_QUIET_MS, st

    @staticmethod
    def _gate_error(code, path, v):
        return SwitchError(code, json.dumps({'still': path, 'reason': v.get('reason'), 'scores': v.get('scores')}))

    def launch(self):
        if self.require_quiet:
            quiet, st = self._game_quiet()
            if not quiet:
                raise SwitchError('app_running', f'scene receiver says {st.get("gate")} (age {st.get("age_ms")} ms)')
        steps = []
        self._tap_system('HOME')
        steps.append('HOME')
        time.sleep(self.settle_s)
        path, v = self._still('launch-home')
        steps.append({'still': path, 'gate': v})
        if not v['ok']:
            raise self._gate_error('not_home_first_tile', path, v)
        if self.require_quiet:
            quiet, st = self._game_quiet()
            if not quiet:
                raise SwitchError('app_running', f'a scene report arrived during the sequence ({st.get("gate")})')
        self._tap_system('A')
        steps.append('A')
        return {'sequence': steps, 'still': path}

    def terminate(self):
        steps = []
        self._tap_system('HOME')
        steps.append('HOME')
        time.sleep(self.settle_s)
        path, v = self._still('terminate-home')
        steps.append({'still': path, 'gate': v})
        if not v['ok']:
            raise self._gate_error('not_home_first_tile', path, v)
        self._tap_system('X')
        steps.append('X')
        time.sleep(self.settle_s)
        path_x, vx = self._still('terminate-dialog')
        steps.append({'still': path_x, 'gate': vx})
        if vx['ok']:
            # The screen did not change: no close dialog (nothing suspended). A would launch the tile: stop here.
            raise SwitchError('close_dialog_not_seen', json.dumps({'still': path_x}))
        self._tap_system('A')
        steps.append('A')
        stopped = None
        if self.require_quiet or self.scene_status().get('gate') not in ('no_scene_receiver', None):
            deadline, stopped = time.monotonic() + TERMINATE_WAIT_S, False
            while time.monotonic() < deadline:
                if self.scene_status().get('gate') in ('no_scene_report', 'scene_report_stale'):
                    stopped = True
                    break
                time.sleep(0.25)
        return {'sequence': steps, 'scene_stopped': stopped}

    def dismiss(self):
        path, v = self._still('dismiss-before')
        if v['ok']:
            raise SwitchError('nothing_to_dismiss', json.dumps({'still': path, 'reason': 'HOME first tile: A would '
                                                                'launch'}))
        self._tap_system('A')
        time.sleep(self.settle_s)
        after, va = self._still('dismiss-after')
        return {'sequence': ['A'], 'still_before': path, 'still_after': after, 'home_after': va['ok']}
