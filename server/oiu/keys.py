"""Key strokes per hardware keyboard layout and chords as (modifier bits, HID keyboard usage)."""
import math


def stroke(character):
    if len(character) != 1 or ord(character) > 127:
        raise ValueError('Typing supports US-layout ASCII only')
    if 'a' <= character <= 'z': return 0, ord(character) - 93
    if 'A' <= character <= 'Z': return 2, ord(character) - 61
    if '1' <= character <= '9': return 0, ord(character) - 19
    plain = {'0': 0x27, ' ': 0x2c, '\n': 0x28, '\r': 0x28, '\t': 0x2b, '\b': 0x2a}
    if character in plain: return 0, plain[character]
    for low, high, usage in [('-', '_', 0x2d), ('=', '+', 0x2e), ('[', '{', 0x2f), (']', '}', 0x30),
                             ('\\', '|', 0x31), (';', ':', 0x33), ("'", '"', 0x34), ('`', '~', 0x35),
                             (',', '<', 0x36), ('.', '>', 0x37), ('/', '?', 0x38)]:
        if character in (low, high): return (2 if character == high else 0), usage
    if character in '!@#$%^&*()':
        index = '!@#$%^&*()'.index(character)
        return 2, 0x1e + index if index < 9 else 0x27
    raise ValueError(f'Unsupported ASCII character U+{ord(character):04X}')


US = {character: (stroke(character),) for character in map(chr, range(32, 127))} | {
    character: (stroke(character),) for character in '\n\r\t\b'} | {
    'å': ((4, 0x04),), 'Å': ((6, 0x04),), '€': ((6, 0x1F),), '£': ((4, 0x20),), '§': ((4, 0x23),), '°': ((6, 0x25),),
    'ä': ((4, 0x18), (0, 0x04)), 'Ä': ((4, 0x18), (2, 0x04)), 'ö': ((4, 0x18), (0, 0x12)), 'Ö': ((4, 0x18), (2, 0x12)),
    'ü': ((4, 0x18), (0, 0x18)), 'Ü': ((4, 0x18), (2, 0x18)), 'é': ((4, 0x08), (0, 0x08)), 'É': ((4, 0x08), (2, 0x08))}
SWEDISH = {character: keys for character, keys in US.items() if character.isalnum() or character in ' \n\r\t\b'} | {
    character: ((modifier, usage),) for character, (modifier, usage) in {
        '!': (2, 0x1E), '"': (2, 0x1F), '#': (2, 0x20), '$': (4, 0x21), '%': (2, 0x22), '&': (2, 0x23),
        "'": (0, 0x31), '(': (2, 0x25), ')': (2, 0x26), '*': (2, 0x31), '+': (0, 0x2D), ',': (0, 0x36),
        '-': (0, 0x38), '.': (0, 0x37), '/': (2, 0x24), ':': (2, 0x37), ';': (2, 0x36), '<': (0, 0x35),
        '=': (2, 0x27), '>': (2, 0x35), '?': (2, 0x2D), '@': (4, 0x1F), '[': (4, 0x25), '\\': (6, 0x24),
        ']': (4, 0x26), '^': (6, 0x30), '_': (2, 0x38), '`': (6, 0x2E), '{': (6, 0x25), '|': (4, 0x24),
        '}': (6, 0x26), 'å': (0, 0x2F), 'Å': (2, 0x2F), 'ä': (0, 0x34), 'Ä': (2, 0x34), 'ö': (0, 0x33),
        'Ö': (2, 0x33), '€': (2, 0x21), '£': (4, 0x20), '§': (0, 0x64), '°': (2, 0x64)}.items()} | {
    '~': ((4, 0x30), (0, 0x2C)), 'é': ((0, 0x2E), (0, 0x08)), 'É': ((0, 0x2E), (2, 0x08)),
    'ü': ((0, 0x30), (0, 0x18)), 'Ü': ((0, 0x30), (2, 0x18))}
LAYOUTS = dict(us=US, sv=SWEDISH)


def strokes(text, layout):
    table = LAYOUTS[layout]
    missing = sorted({character for character in text if character not in table})
    if missing: raise ValueError(f'The {layout} keyboard layout cannot type: ' + ' '.join(map(repr, missing)))
    return [key for character in text for key in table[character]]


def chord(value, layout):
    if not isinstance(value, str): raise ValueError('Key must be a string')
    parts = value.lower().split('+')
    modifier = 0
    modifiers = dict(ctrl=1, control=1, shift=2, alt=4, option=4, opt=4, cmd=8, command=8, gui=8, super=8)
    for part in parts[:-1]:
        if part not in modifiers: raise ValueError(f'Unknown key modifier: {part}')
        modifier |= modifiers[part]
    keys = dict(enter=0x28, tab=0x2b, escape=0x29, backspace=0x2a, delete=0x4c, space=0x2c,
                up=0x52, down=0x51, left=0x50, right=0x4f, end=0x4d, pageup=0x4b, pagedown=0x4e)
    alias = {'return': 'enter', 'ret': 'enter', 'esc': 'escape', 'del': 'delete', 'bksp': 'backspace',
             'spacebar': 'space', 'pgup': 'pageup', 'pgdn': 'pagedown', 'arrowup': 'up', 'arrowdown': 'down',
             'arrowleft': 'left', 'arrowright': 'right'}
    key = alias.get(parts[-1], parts[-1])
    if key in keys: return modifier, keys[key]
    if len(key) == 1:
        typed = LAYOUTS[layout].get(key)
        if not typed: raise ValueError(f'The {layout} keyboard layout has no key for {key!r}')
        if len(typed) > 1: raise ValueError(f'{key!r} needs several keystrokes on the {layout} layout; use type_text')
        extra, usage = typed[0]
        return modifier | extra, usage
    raise ValueError(f'Unknown key: {key}. Accepted: return/enter, tab, esc/escape, del/delete, bksp/backspace, space, pgup/pageup, pgdn/pagedown, up/down/left/right, cmd/ctrl/shift/alt+<key>, a single character, or a button: home, lock, volume_up, volume_down, mute, siri')


def fraction(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Coordinates must be finite screen fractions 0…1')
    return float(value)
