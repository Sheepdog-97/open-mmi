#!/usr/bin/env python3
"""Open MMI vehicle status CLI dashboard.

Reads the persistent status snapshot written by canbusd.status_bus and renders a
small terminal dashboard. This is intentionally read-only: it does not subscribe
to CAN directly and cannot affect daemon behaviour.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
STALE_AFTER_SECONDS = 3.0
DEFAULT_REFRESH_SECONDS = 0.5

def _default_status_path() -> Path:
    runtime_dir = os.getenv('XDG_RUNTIME_DIR')
    if runtime_dir:
        return Path(runtime_dir) / 'open-mmi' / 'status.json'
    return Path('/tmp/open-mmi-status.json')
STATUS_PATH = Path(os.getenv('OPEN_MMI_STATUS_PATH', str(_default_status_path())))

class Theme:

    def __init__(self, colour: bool=True, symbols: bool=True):
        self.colour_enabled = colour
        self.symbols_enabled = symbols

    def colour(self, text: Any, code: str) -> str:
        value = str(text)
        if not self.colour_enabled:
            return value
        return f'\x1b[{code}m{value}\x1b[0m'

    def bold(self, text: Any) -> str:
        return self.colour(text, '1')

    def dim(self, text: Any) -> str:
        return self.colour(text, '2')

    def red(self, text: Any) -> str:
        return self.colour(text, '31')

    def green(self, text: Any) -> str:
        return self.colour(text, '32')

    def amber(self, text: Any) -> str:
        return self.colour(text, '33')

    def blue(self, text: Any) -> str:
        return self.colour(text, '34')

    def magenta(self, text: Any) -> str:
        return self.colour(text, '35')

    def cyan(self, text: Any) -> str:
        return self.colour(text, '36')

    def white(self, text: Any) -> str:
        return self.colour(text, '37')

    def sym(self, name: str, fallback: str) -> str:
        if not self.symbols_enabled:
            return fallback
        return {'ok': '✓', 'bad': '✕', 'warn': '⚠', 'dot': '●', 'empty': '○', 'door': '🚪', 'car': '▣', 'left': '◀', 'right': '▶', 'both': '◀▶', 'steer_left': '↶', 'steer_right': '↷', 'steer_center': '↔', 'light_off': '○', 'sides': '◐', 'dip': '◉', 'main': '●', 'brake': '●', 'reverse': 'R', 'park': 'P', 'bulb': '⚠'}.get(name, fallback)

def _load_status(path: Path=STATUS_PATH) -> Dict[str, Any]:
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'updated_at': None, 'state': {}, 'error': 'status file not found'}
    except json.JSONDecodeError:
        return {'updated_at': None, 'state': {}, 'error': 'invalid status json'}
    except Exception as exc:
        return {'updated_at': None, 'state': {}, 'error': str(exc)}

def _get_path(data: Dict[str, Any], dotted: str, default: Any='-') -> Any:
    current: Any = data
    for part in dotted.split('.'):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current

def _plain_bool(value: Any, true: str='ON', false: str='OFF', unknown: str='-') -> str:
    if value is True:
        return true
    if value is False:
        return false
    return unknown

def _state_label(value: Any, theme: Theme, true_text: str='ON', false_text: str='OFF', unknown_text: str='-', true_colour: str='amber', false_colour: str='green', true_symbol: str='dot', false_symbol: str='ok') -> str:
    if value is True:
        return getattr(theme, true_colour)(f"{theme.sym(true_symbol, '*')} {true_text}")
    if value is False:
        return getattr(theme, false_colour)(f"{theme.sym(false_symbol, '-')} {false_text}")
    return theme.dim(unknown_text)

def _open_label(value: Any, theme: Theme) -> str:
    if value is True:
        return theme.amber(f"{theme.sym('warn', '!')} OPEN")
    if value is False:
        return theme.green(f"{theme.sym('ok', 'ok')} closed")
    return theme.dim('-')

def _yes_no(value: Any, theme: Theme, yes_colour: str='green', no_colour: str='red') -> str:
    if value is True:
        return getattr(theme, yes_colour)(f"{theme.sym('ok', 'yes')} yes")
    if value is False:
        return getattr(theme, no_colour)(f"{theme.sym('bad', 'no')} no")
    return theme.dim('-')

def _age(payload: Dict[str, Any]) -> Optional[float]:
    updated_at = payload.get('updated_at')
    if updated_at is None:
        return None
    try:
        return max(0.0, time.time() - float(updated_at))
    except (TypeError, ValueError):
        return None

def _state_health(payload: Dict[str, Any], theme: Theme) -> str:
    if payload.get('error'):
        return theme.red(f"{theme.sym('bad', 'ERR')} ERROR")
    age = _age(payload)
    if age is None:
        return theme.amber(f"{theme.sym('empty', '?')} WAITING")
    if age > STALE_AFTER_SECONDS:
        return theme.amber(f"{theme.sym('warn', '!')} STALE")
    return theme.green(f"{theme.sym('dot', '*')} LIVE")

def _clear() -> None:
    print('\x1b[2J\x1b[H', end='')

def _line(title: str='') -> str:
    if not title:
        return '─' * 74
    label = f' {title} '
    return label.center(74, '─')

def _pair(left_label: str, left_value: Any, right_label: str='', right_value: Any='') -> str:
    left = f'{left_label:<20} {str(left_value):<22}'
    if right_label:
        right = f'{right_label:<20} {str(right_value):<22}'
        return f'{left} {right}'
    return left

def _degrees_label(value: Any, theme: Theme) -> str:
    if not isinstance(value, (int, float)):
        return theme.dim('-')
    if value > 0:
        return theme.cyan(f"{theme.sym('steer_right', '>')} {value:.2f}° right")
    if value < 0:
        return theme.cyan(f"{theme.sym('steer_left', '<')} {abs(value):.2f}° left")
    return theme.green(f"{theme.sym('steer_center', '=')} 0.00° center")

def _lighting_mode_label(mode: Any, theme: Theme) -> str:
    if not isinstance(mode, str):
        return theme.dim('-')
    if mode == 'off':
        return theme.dim(f"{theme.sym('light_off', 'off')} off")
    if 'main_beam' in mode:
        return theme.blue(f"{theme.sym('main', 'main')} {mode}")
    if 'rear_fog' in mode:
        return theme.magenta(f"{theme.sym('warn', 'fog')} {mode}")
    if 'dip' in mode:
        return theme.cyan(f"{theme.sym('dip', 'dip')} {mode}")
    if 'sides' in mode:
        return theme.amber(f"{theme.sym('sides', 'side')} {mode}")
    if 'reverse' in mode:
        return theme.amber(f"{theme.sym('reverse', 'R')} {mode}")
    if mode == 'unknown':
        return theme.amber(f"{theme.sym('warn', '?')} unknown")
    return str(mode)

def _indicator_label(lighting: Dict[str, Any], theme: Theme) -> str:
    left = lighting.get('left_indicator')
    right = lighting.get('right_indicator')
    hazards = lighting.get('hazards')
    if hazards:
        return theme.amber(f"{theme.sym('warn', '!')} {theme.sym('both', '<>')} hazards")
    if left and right:
        return theme.amber(f"{theme.sym('both', '<>')} both")
    if left:
        return theme.amber(f"{theme.sym('left', '<')} left")
    if right:
        return theme.amber(f"{theme.sym('right', '>')} right")
    if left is False or right is False:
        return theme.green(f"{theme.sym('ok', 'ok')} off")
    return theme.dim('-')

def _render_dashboard(payload: Dict[str, Any], path: Path=STATUS_PATH, theme: Optional[Theme]=None) -> None:
    theme = theme or Theme()
    state = payload.get('state', {})
    age = _age(payload)
    doors = state.get('doors', {}) if isinstance(state.get('doors', {}), dict) else {}
    vehicle = state.get('vehicle', {}) if isinstance(state.get('vehicle', {}), dict) else {}
    lighting = state.get('lighting', {}) if isinstance(state.get('lighting', {}), dict) else {}
    climate = state.get('climate', {}) if isinstance(state.get('climate', {}), dict) else {}
    fuel = state.get('fuel', {}) if isinstance(state.get('fuel', {}), dict) else {}
    engine = state.get('engine', {}) if isinstance(state.get('engine', {}), dict) else {}
    electrical = state.get('electrical', {}) if isinstance(state.get('electrical', {}), dict) else {}
    steering = state.get('steering', {}) if isinstance(state.get('steering', {}), dict) else {}
    _clear()
    print(theme.bold('Open MMI Vehicle Status'))
    print(_line())
    if age is None:
        age_text = theme.dim('never')
    elif age > STALE_AFTER_SECONDS:
        age_text = theme.amber(f'{age:.1f}s ago')
    else:
        age_text = theme.green(f'{age:.1f}s ago')
    print(_pair('Status', _state_health(payload, theme), 'Last update', age_text))
    print(_pair('Vehicle present', _yes_no(_get_path(vehicle, 'present', None), theme), 'Snapshot', str(path)))
    if payload.get('error'):
        print()
        print(_line('Error'))
        print(theme.red(payload['error']))
        print()
        print('Press Ctrl+C to exit.')
        return
    print()
    print(_line('Doors'))
    print(_pair(f"{theme.sym('door', 'D')} Front left", _open_label(doors.get('front_left'), theme), f"{theme.sym('door', 'D')} Front right", _open_label(doors.get('front_right'), theme)))
    print(_pair(f"{theme.sym('door', 'D')} Rear left", _open_label(doors.get('rear_left'), theme), f"{theme.sym('door', 'D')} Rear right", _open_label(doors.get('rear_right'), theme)))
    print(_pair('Boot', _open_label(doors.get('boot'), theme), 'Bonnet', _open_label(doors.get('bonnet'), theme)))
    print(_pair('Any open', _yes_no(doors.get('any_open'), theme, yes_colour='amber', no_colour='green'), 'Raw', doors.get('raw', '-')))
    print()
    print(_line('Vehicle'))
    print(_pair(f"{theme.sym('reverse', 'R')} Reverse", _state_label(vehicle.get('reverse'), theme, true_text='ON', false_text='OFF', true_colour='amber'), f"{theme.sym('park', 'P')} Handbrake", _state_label(vehicle.get('handbrake'), theme, true_text='ON', false_text='OFF', true_colour='amber')))
    print(_pair('Speed', _speed_label(vehicle.get('speed_kmh')), 'Speed raw', vehicle.get('speed_raw', '-')))
    print(_pair('Odometer', _distance_label(vehicle.get('odometer_km'), decimals=0), 'Odo raw', vehicle.get('odometer_raw', '-')))
    print(_pair('Reverse raw', vehicle.get('reverse_raw', '-'), 'Handbrake raw', vehicle.get('handbrake_raw', '-')))
    print()
    print(_line('Fuel'))
    fuel_level = fuel.get('level_l')
    fuel_level_text = '-' if fuel_level is None else f'{fuel_level:g} L'
    print(_pair('Level', fuel_level_text, 'Level raw', fuel.get('level_raw', '-')))
    print(_pair('Low-fuel warning', _state_label(fuel.get('low_level_warning'), theme, true_text='ON', false_text='OFF', true_colour='amber'), '', ''))
    print()
    engine_speed = engine.get("speed_rpm")
    supply_voltage = electrical.get("supply_voltage_v", electrical.get("terminal30_voltage_v"))
    supply_voltage_raw = electrical.get("supply_voltage_raw", electrical.get("terminal30_voltage_raw", "-"))

    print(_line("Engine / Electrical"))
    print(_pair("RPM", _rpm_label(engine_speed), "RPM raw", engine.get("speed_raw", "-")))
    print(_pair("Coolant", _temperature_label(engine.get("coolant_temp_c")), "Coolant raw", engine.get("coolant_temp_raw", "-")))
    print(_pair("Supply voltage", _voltage_label(supply_voltage), "Voltage raw", supply_voltage_raw))
    print()
    print(_line("Climate"))
    print(_pair("Blower", _percent_label(climate.get("blower_load_percent")), "Raw", climate.get("blower_load_raw", "-")))
    print(_pair("Outside reg", _temperature_label(climate.get("outside_temp_regulation_c")), "Outside raw", climate.get("outside_temp_regulation_raw", "-")))
    print(_pair("Outside unfiltered", _temperature_label(climate.get("outside_temp_unfiltered_c")), "Unfiltered raw", climate.get("outside_temp_unfiltered_raw", "-")))
    print(_pair("Rear heater", _bool_label(climate.get("rear_window_heater_requested")), "Klima raw", climate.get("klima_status_raw", "-")))
    recirculation = climate.get('recirculation_active', climate.get('front_demist_air_request'))
    recirculation_raw = climate.get('recirculation_raw', climate.get('front_demist_air_request_raw', '-'))
    print(_pair('Re-circulation', _bool_label(recirculation), 'Recirc raw', recirculation_raw))
    print(_pair("Compressor", _bool_label(climate.get("compressor_active")), "Front screen", _bool_label(climate.get("front_windscreen_heater_requested"))))
    print()
    print(_line('Steering'))
    print(_pair('Angle', _degrees_label(steering.get('angle_degrees'), theme), 'Direction', steering.get('direction', '-')))
    print(_pair('Raw', steering.get('angle_raw', '-'), 'Magnitude', steering.get('angle_magnitude_raw', '-')))
    print()
    print(_line('Lighting'))
    dimmer = lighting.get('dimmer_percent', '-')
    if dimmer != '-':
        dimmer = f'{dimmer}%'
    print(_pair('Mode', _lighting_mode_label(lighting.get('mode'), theme), 'Dimmer', dimmer))
    print(_pair('Lights on', _state_label(lighting.get('lights_on'), theme, true_text='ON', false_text='OFF', true_colour='cyan'), f"{theme.sym('bulb', '!')} Bulb out", _state_label(lighting.get('bulb_out'), theme, true_text='FAULT', false_text='ok', true_colour='red', false_colour='green', true_symbol='bad')))
    print(_pair(f"{theme.sym('brake', '*')} Brake", _state_label(lighting.get('brake'), theme, true_text='ON', false_text='OFF', true_colour='red'), 'Indicators', _indicator_label(lighting, theme)))
    print(_pair('Hazards', _state_label(lighting.get('hazards'), theme, true_text='ON', false_text='OFF', true_colour='amber', true_symbol='warn'), 'Bulb raw', lighting.get('bulb_out_raw', '-')))
    print(_pair('Mode raw', lighting.get('mode_raw', '-'), 'Secondary raw', lighting.get('secondary_raw', '-')))
    print()
    print(_line())
    print(theme.dim('Press Ctrl+C to exit.'))

def _temperature_label(value: Any) -> str:
    if value is None or value == '-':
        return '-'
    try:
        return f'{float(value):.1f} °C'
    except (TypeError, ValueError):
        return str(value)

def _voltage_label(value: Any) -> str:
    if value is None or value == '-':
        return '-'
    try:
        return f'{float(value):.2f} V'
    except (TypeError, ValueError):
        return str(value)

def _rpm_label(value: Any) -> str:
    if value is None or value == "-":
        return "-"
    try:
        return f"{float(value):.0f} rpm"
    except (TypeError, ValueError):
        return str(value)


def _bool_label(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "-" if value is None else str(value)


def _percent_label(value: Any) -> str:
    if value is None or value == '-':
        return '-'
    try:
        return f'{float(value):.1f}%'
    except (TypeError, ValueError):
        return str(value)

def _speed_unit() -> str:
    unit = __import__('os').environ.get('OPEN_MMI_SPEED_UNIT', 'mph').strip().lower()
    if unit in ('kmh', 'kph', 'km/h'):
        return 'kmh'
    if unit == 'both':
        return 'both'
    return 'mph'

def _speed_label(speed_kmh: Any) -> str:
    if speed_kmh is None or speed_kmh == '-':
        return '-'
    try:
        kmh = float(speed_kmh)
    except (TypeError, ValueError):
        return str(speed_kmh)
    mph = kmh * 0.621371
    unit = _speed_unit()
    if unit == 'kmh':
        return f'{kmh:.1f} km/h'
    if unit == 'both':
        return f'{mph:.1f} mph / {kmh:.1f} km/h'
    return f'{mph:.1f} mph'

def _distance_label(distance_km: Any, decimals: int=0) -> str:
    if distance_km is None or distance_km == '-':
        return '-'
    try:
        km = float(distance_km)
    except (TypeError, ValueError):
        return str(distance_km)
    miles = km * 0.621371192
    unit = _speed_unit()
    if unit == 'kmh':
        return f'{km:,.{decimals}f} km'
    if unit == 'both':
        return f'{miles:,.{decimals}f} mi / {km:,.{decimals}f} km'
    return f'{miles:,.{decimals}f} mi'

def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    return str(value)

def _print_tree(value: Any, indent: int=0) -> None:
    pad = '  ' * indent
    if isinstance(value, dict):
        for key in sorted(value.keys()):
            child = value[key]
            if isinstance(child, dict):
                print(f'{pad}{key}:')
                _print_tree(child, indent + 1)
            else:
                print(f'{pad}{key}: {_format_value(child)}')
    else:
        print(f'{pad}{_format_value(value)}')

def _render_raw(payload: Dict[str, Any], path: Path=STATUS_PATH, theme: Optional[Theme]=None) -> None:
    theme = theme or Theme()
    _clear()
    print(theme.bold('Open MMI Raw Status'))
    print('===================')
    print()
    state = payload.get('state', {})
    if state:
        _print_tree(state)
    else:
        print('No status received yet.')
    print()
    age = _age(payload)
    print(f'Health: {_state_health(payload, theme)}')
    last_update = 'never' if age is None else f'{age:.1f}s ago'
    print(f'Last update: {last_update}')
    print(f'Status file: {path}')
    if payload.get('error'):
        print(f"Error: {payload['error']}")

def _iter_once_or_forever(once: bool) -> Iterable[None]:
    yield None
    while not once:
        yield None

def main() -> None:
    parser = argparse.ArgumentParser(description='Show Open MMI vehicle status')
    parser.add_argument('--raw', action='store_true', help='show generic raw state tree')
    parser.add_argument('--once', action='store_true', help='render once and exit')
    parser.add_argument('--path', type=Path, default=STATUS_PATH, help='status JSON path')
    parser.add_argument('--refresh', type=float, default=DEFAULT_REFRESH_SECONDS, help='refresh interval in seconds')
    parser.add_argument('--no-colour', '--no-color', dest='no_colour', action='store_true', help='disable ANSI colours')
    parser.add_argument('--no-symbols', action='store_true', help='disable Unicode symbols')
    args = parser.parse_args()
    colour = not args.no_colour and sys.stdout.isatty()
    symbols = not args.no_symbols
    theme = Theme(colour=colour, symbols=symbols)
    try:
        for _ in _iter_once_or_forever(args.once):
            payload = _load_status(args.path)
            if args.raw:
                _render_raw(payload, args.path, theme)
            else:
                _render_dashboard(payload, args.path, theme)
            if not args.once:
                time.sleep(max(0.1, args.refresh))
    except KeyboardInterrupt:
        print()
        sys.exit(0)
if __name__ == '__main__':
    main()
