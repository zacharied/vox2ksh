from enum import Enum, auto
from collections import namedtuple
from recordclass import dataobject
from xml.etree import ElementTree
from shutil import copyfile
from functools import lru_cache as cache
from os.path import splitext as splitx
import traceback
import random
import math

import sys, os
import argparse

TICKS_PER_BEAT = 48

pprint_prefix = ''

def pprint(*args, **kwargs):
    print('(' + pprint_prefix + ') '.join(map(str, args)), **kwargs)

TimeSignature = namedtuple('TimeSignature', 'top bottom')

class VoxNameError(Exception):
    pass
class MetadataFindError(Exception):
    pass
class AudioFileFindError(Exception):
    pass
class LaserNodeFormatError(Exception):
    pass
class ButtonEventError(Exception):
    pass
class KshConversionError(Exception):
    pass

class Timing:
    def __init__(self, measure, beat, offset):
        self.measure = measure
        self.beat = beat
        self.offset = offset

    @classmethod
    def from_time_str(cls, time: str):
        """ Create a Timing from the format string that appears in the first column of vox tracks. """
        splitted = time.split(',')
        if int(splitted[2]) >= TICKS_PER_BEAT:
            raise ValueError('offset greater than maximum')
        return cls(int(splitted[0]), int(splitted[1]), int(splitted[2]))

    # TODO I think most of these could be implemented better.
    def __eq__(self, other):
        return self.measure == other.measure and self.beat == other.beat and self.offset == other.offset

    def __hash__(self):
        return hash((self.measure, self.beat, self.offset))

    def __str__(self):
        return '{},{},{}'.format(self.measure, self.beat, self.offset)

    def __cmp__(self, other):
        if self.measure == other.measure:
            if self.beat == other.beat:
                return self.offset - other.offset
            return self.beat - other.beat
        return self.measure - other.measure

class CameraParam(Enum):
    @classmethod
    def from_vox_name(cls, vox_name):
        if vox_name == 'CAM_RotX':
            return cls.ROT_X
        elif vox_name == 'CAM_Radi':
            return cls.RAD_I
        else:
            return None

    def to_ksh_name(self):
        if self == self.ROT_X:
            return 'zoom_top'
        elif self == self.RAD_I:
            return 'zoom_bottom'

    def scaling_factor(self):
        if self == self.ROT_X:
            return 150.0
        elif self == self.RAD_I:
            return -150.0

    ROT_X = auto()
    RAD_I = auto()

class KshootEffect(Enum):
    def to_ksh_name(self, params):
        if self == KshootEffect.RETRIGGER:
            division = 8 if params is None else params['division']
            return f'Retrigger;{division}'

        elif self == KshootEffect.GATE:
            division = 8 if params is None else params['division']
            return f'Gate;{division}'

        elif self == KshootEffect.FLANGER:
            return 'Flanger'

        elif self == KshootEffect.BITCRUSHER:
            degree = 10 if params is None else params['degree']
            return f'BitCrusher;{degree}'

        elif self == KshootEffect.PHASER:
            return 'Phaser'

        elif self == KshootEffect.WOBBLE:
            division = 12 if params is None else params['division']
            return f'Wobble;{division}'

        elif self == KshootEffect.PITCHSHIFT:
            tones = 12 if params is None else params['tones']
            return f'PitchShift;{tones}'

        elif self == KshootEffect.TAPESTOP:
            speed = 50 if params is None else params['speed']
            return f'TapeStop;{speed}'

        elif self == KshootEffect.ECHO:
            # TODO Figure out echo parameters.
            x = 4 if params is None else params['x']
            y = 60 if params is None else params['y']
            return f'Echo;{x};{y}'

        elif self == KshootEffect.SIDECHAIN:
            return 'SideChain'

    @classmethod
    def from_pre_v4_vox_sound_id(cls, sound_id):
        if sound_id < 2:
            if sound_id == 1:
                print('A chart used 0bt_2mix. Please make sure the chart was "pulse_laser_higedriver_1n".')
            return None
        elif sound_id == 2:
            return cls.RETRIGGER, {"division": 8}
        elif sound_id == 3:
            return cls.RETRIGGER, {"division": 16}
        elif sound_id == 4:
            return cls.GATE, {"division": 16}
        elif sound_id == 5:
            return cls.FLANGER
        elif sound_id == 6:
            return cls.RETRIGGER, {"division": 32}
        elif sound_id == 7:
            return cls.GATE, {"division": 8}
        elif sound_id == 8:
            return cls.PITCHSHIFT
        else:
            raise ValueError(f'Pre-v4 vox sound id {sound_id} does not exist.')

    @classmethod
    def choose_random(cls):
        choice = random.choice(list(cls))
        if choice == cls.TAPESTOP or choice == cls.PITCHSHIFT:
            return cls.choose_random()
        return choice

    RETRIGGER = auto()
    GATE = auto()
    FLANGER = auto()
    BITCRUSHER = auto()
    PHASER = auto()
    WOBBLE = auto()
    PITCHSHIFT = auto()
    TAPESTOP = auto()
    ECHO = auto()
    SIDECHAIN = auto()

class Button(Enum):
    BT_A = auto()
    BT_B = auto()
    BT_C = auto()
    BT_D = auto()
    FX_L = auto()
    FX_R = auto()

    def is_fx(self):
        return self == Button.FX_L or self == Button.FX_R

    @classmethod
    def from_track_num(cls, num: int):
        if num == 2:
            return cls.FX_L
        elif num == 3:
            return cls.BT_A
        elif num == 4:
            return cls.BT_B
        elif num == 5:
            return cls.BT_C
        elif num == 6:
            return cls.BT_D
        elif num == 7:
            return cls.FX_R
        else:
            raise ValueError('invalid track number for button: {}'.format(num))

    def to_track_num(self):
        if self == self.FX_L:
            return 2
        elif self == self.FX_R:
            return 7
        elif self == self.BT_A:
            return 3
        elif self == self.BT_B:
            return 4
        elif self == self.BT_C:
            return 5
        elif self == self.BT_D:
            return 6

class ButtonPress:
    def __init__(self, button: Button, duration: int, effect: (KshootEffect, dict)):
        self.button = button
        self.duration = duration
        self.effect = effect

LaserRangeChange = namedtuple('LaserRangeChange', 'time side range')

class LaserSide(Enum):
    LEFT = auto()
    RIGHT = auto()

    def to_letter(self):
        return 'l' if self == LaserSide.LEFT else 'r'

    def to_track_num(self):
        return 1 if self == self.LEFT else 8

class LaserCont(Enum):
    """ The continuity status of a laser node. """
    CONTINUE = 0
    START = 1
    END = 2

class LaserNode:
    class Builder(dataobject):
        side: LaserSide = None
        position: int = None
        node_type: LaserCont = None
        range: int = 1

    def __init__(self, builder: Builder):
        self.side = builder.side
        self.position = builder.position
        self.node_type = builder.node_type
        self.range = builder.range

    def position_ksh(self):
        """ Convert the position from the 7-bit scale to whatever the hell KSM is using. """
        chars = []
        for char in range(10):
            chars.append(chr(ord('0') + char))
        for char in range(24):
            chars.append(chr(ord('A') + char))
        for char in range(15):
            chars.append(chr(ord('a') + char))
        idx = math.floor((self.position / 127) * (len(chars) - 1))
        return chars[idx]

class LaserSlam:
    def __init__(self, start: LaserNode, end: LaserNode):
        self.start = start
        self.end = end

class RevMap:
    def __init__(self, mapping):
        self.mapping = mapping
        self._rev = {v: k for k, v in mapping.items()}

    def get(self, k):
        return self.mapping.get(k)

    def rev(self, k):
        return self._rev.get(k)

class Difficulty(Enum):
    NOVICE = 0
    ADVANCED = 1
    EXHAUST = 2
    MAXIMUM = 3
    INFINITE = 4
    # TODO GRV and HVN?

    @classmethod
    @cache(maxsize=None)
    def _letter(cls):
        return RevMap({
            cls.NOVICE: 'n',
            cls.ADVANCED: 'a',
            cls.EXHAUST: 'e',
            cls.MAXIMUM: 'm',
            cls.INFINITE: 'i'
        })

    @classmethod
    def from_letter(cls, k):
        return cls._letter().rev(k)

    def to_letter(self):
        return self._letter().get(self)

    def to_ksh_name(self):
        """ Convert to a name recognized by KSM. """
        if self == self.NOVICE:
            return 'novice'
        elif self == self.ADVANCED:
            return 'challenge'
        elif self == self.EXHAUST:
            return 'extended'
        elif self == self.MAXIMUM or self == self.INFINITE:
            return 'infinite'

    def to_xml_name(self):
        if self == self.NOVICE:
            return 'novice'
        elif self == self.ADVANCED:
            return 'advanced'
        elif self == self.EXHAUST:
            return 'exhaust'
        elif self == self.MAXIMUM:
            return 'maximum'
        elif self == self.INFINITE:
            return 'infinite'

    def to_jacket_ifs_numer(self):
        if self == self.NOVICE:
            return 1
        elif self == self.ADVANCED:
            return 2
        elif self == self.EXHAUST:
            return 3
        elif self == self.MAXIMUM:
            return 4
        else:
            return 5

class ParserError(Exception):
    """ Exception raised when the Vox parser encounters invalid syntax. """
    def __init__(self, message, filename, line):
        super().__init__(f'({filename}:{str(line)} {message}')

class EventKind(Enum):
    TRACK = auto()
    TIMESIG = auto()
    BPM = auto()
    TILTMODE = auto()
    SPCONTROLLER = auto()

class EventsOfType:
    def __init__(self, event_filtered):
        self.itr = dict({ key[1]: value for key, value in event_filtered.items() })

    def with_time(self, timing):
        return self.itr.get(timing)

class KshLineBuf:
    class ButtonState(Enum):
        NONE = auto()
        PRESS = auto()
        HOLD = auto()

    def __init__(self):
        self.buttons = {}
        self.lasers = {}
        self.meta = []

        for bt in list(Button):
            self.buttons[bt] = self.ButtonState.NONE

        for side in list(LaserSide):
            self.lasers[side] = '-'

    def out(self):
        buf = ''

        for m in self.meta:
            buf += m + '\n'

        for bt in [ Button.BT_A, Button.BT_B, Button.BT_C, Button.BT_D ]:
            if self.buttons[bt] == self.ButtonState.HOLD:
                buf += '2'
            elif self.buttons[bt] == self.ButtonState.PRESS:
                buf += '1'
            else:
                buf += '0'

        buf += '|'

        for bt in [ Button.FX_L, Button.FX_R ]:
            if self.buttons[bt] == self.ButtonState.HOLD:
                buf += '1'
            elif self.buttons[bt] == self.ButtonState.PRESS:
                buf += '2'
            else:
                buf += '0'

        buf += '|'

        for laser in [ LaserSide.LEFT, LaserSide.RIGHT ]:
            buf += self.lasers[laser]

        return buf


class Vox:
    class State(Enum):
        @classmethod
        def from_token(cls, token):
            if token.startswith('=') or token.startswith(' '):
                return None
            elif token == 'END':
                return cls.NONE
            if token == 'FORMAT VERSION':
                return cls.FORMAT_VERSION
            elif token == 'BPM':
                return cls.BPM
            elif token == 'BPM INFO':
                return cls.BPM_INFO
            elif token == 'BEAT INFO':
                return cls.BEAT_INFO
            elif token == 'END POSISION' or token == 'END POSITION':
                return cls.END_POSITION
            elif token == 'SOUND ID START':
                return cls.SOUND_ID
            elif token == 'SPCONTROLER' or token == 'SPCONTROLLER':
                return cls.SPCONTROLLER
            elif token == 'TRACK AUTO TAB':
                return None
            elif token.startswith('TRACK'):
                return cls.TRACK, int(token[5])

        NONE = auto()
        FORMAT_VERSION = auto()
        BPM = auto()
        BPM_INFO = auto()
        BEAT_INFO = auto()
        END_POSITION = auto()
        SOUND_ID = auto()
        TRACK = auto()
        SPCONTROLLER = auto()

    def __init__(self):
        self.voxfile = None
        self.game_id = 0
        self.song_id = 0
        self.vox_version = 0
        self.defines = {}
        self.end = None
        self.events = {}

        self.last_time = Timing(1, 1, 0)
        self.new_laser = False

        self.state = None
        self.state_track = 0

        self.metadata: ElementTree = None
        self.difficulty = None
        self.difficulty_idx = 0

        self.finalized = False
        self._events_track = {}
        self._events_bpm = []
        self._events_timesig = []
        self._events_tiltmode = []
        self._events_spcontroller = {}

    def diff_token(self):
        return str(self.difficulty_idx) + self.difficulty.to_letter()

    def get_metadata(self, tag, from_diff=False):
        if from_diff:
            the_diff = None
            for diff in self.metadata.find('difficulty').iter():
                if diff.tag == self.difficulty.to_xml_name():
                    the_diff = diff
                    break
            if the_diff is None:
                raise LookupError('difficulty {} not found in the `music` element'.format(self.difficulty.to_xml_name()))
            return the_diff.find(tag).text
        return self.metadata.find('info').find(tag).text

    def bpm_string(self):
        # TODO Make sure decimal BPM's are okay.
        if self.get_metadata('bpm_min') == self.get_metadata('bpm_max'):
            return int(int(self.get_metadata('bpm_min')) / 100)
        else:
            return f"{int(int(self.get_metadata('bpm_min')) / 100)}-{int(int(self.get_metadata('bpm_max')) / 100)}"

    def events_track(self, track):
        if track not in self._events_track:
            res = {k[1]: v for k, v in self.events.items() if type(k[0]) is tuple and k[0][0] == EventKind.TRACK and k[0][1] == track}
            if self.finalized:
                self._events_track[track] = res
            return res
        return self._events_track[track]

    def events_bpm(self):
        if len(self._events_bpm) == 0:
            res = {k[1]: v for k, v in self.events.items() if k[0] == EventKind.BPM}
            if self.finalized:
                self._events_bpm = res
            return res
        return self._events_bpm

    def events_timesig(self):
        if len(self._events_timesig) == 0:
            res = {k[1]: v for k, v in self.events.items() if k[0] == EventKind.TIMESIG}
            if self.finalized:
                self._events_timesig = res
            return res
        return self._events_timesig

    def events_tiltmode(self):
        if len(self._events_tiltmode) == 0:
            res = {k[1]: v for k, v in self.events.items() if k[0] == EventKind.TILTMODE}
            if self.finalized:
                self._events_tiltmode = res
            return res
        return self._events_tiltmode

    def events_spcontroller(self, control):
        if control not in self._events_spcontroller:
            res = {k[1]: v for k, v in self.events.items() if type(k[0]) is tuple and k[0][0] == EventKind.SPCONTROLLER and k[0][1] == control}
            if self.finalized:
                self._events_spcontroller[control] = res
            return res
        return self._events_spcontroller[control]

    def process_state(self, line, filename=None, line_no=None):
        splitted = line.split('\t')

        if self.state == self.State.FORMAT_VERSION:
            self.vox_version = int(line)

        elif self.state == self.State.BEAT_INFO:
            timesig = TimeSignature(int(splitted[1]), int(splitted[2]))
            self.events[(EventKind.TIMESIG, Timing.from_time_str(splitted[0]))] = timesig

        elif self.state == self.State.BPM:
            self.events[(EventKind.BPM, Timing(1, 1, 0))] = float(line)

        elif self.state == self.State.BPM_INFO:
            self.events[(EventKind.BPM, Timing.from_time_str(splitted[0]))] = splitted[1]

        elif self.state == self.State.END_POSITION:
            self.end = Timing.from_time_str(line)

        elif self.state == self.State.SOUND_ID:
            raise ParserError('non-define line encountered in SOUND ID', filename, line_no)

        elif self.state == self.State.SPCONTROLLER:
            param = CameraParam.from_vox_name(splitted[1])
            self.events[((EventKind.SPCONTROLLER, param), Timing.from_time_str(splitted[0]))] = float(splitted[4])

        elif self.state == self.state.TRACK:
            if self.state_track == 1 or self.state_track == 8:
                laser_node = LaserNode.Builder()
                laser_node.side = LaserSide.LEFT if self.state_track == 1 else LaserSide.RIGHT
                laser_node.position = int(splitted[1])
                laser_node.node_type = LaserCont(int(splitted[2]))
                if len(splitted) > 5:
                    laser_node.range = int(splitted[5])
                laser_node = LaserNode(laser_node)

                if laser_node.position > 127 or laser_node.position < 0:
                    raise LaserNodeFormatError(f'position {laser_node.position} is out of bounds')

                # Check if it's a slam.
                slam_start = self.events_track(self.state_track).get(Timing.from_time_str(splitted[0]))
                if Timing.from_time_str(splitted[0]) == self.last_time:
                    self.new_laser = False
                else:
                    self.new_laser = True

                events_key = ((EventKind.TRACK, self.state_track), Timing.from_time_str(splitted[0]))
                if self.new_laser:
                    self.events[events_key] = laser_node
                else:
                    if type(slam_start) is LaserSlam:
                        slam_start = slam_start.end
                    slam = LaserSlam(slam_start, laser_node)
                    self.events[events_key] = slam

                self.last_time = Timing.from_time_str(splitted[0])

            else:
                try:
                    button = Button.from_track_num(self.state_track)
                    fx_data = None
                    if button.is_fx():
                        # Process effect assignment.
                        if self.vox_version < 4:
                            sound_id = int(splitted[3]) if splitted[3].isdigit() else int(self.defines[splitted[3]])
                            fx_res = KshootEffect.from_pre_v4_vox_sound_id(sound_id)
                            if type(fx_res) is tuple:
                                fx_data = (fx_res[0], fx_res[1])
                            else:
                                fx_data = (fx_res, None)

                    self.events[((EventKind.TRACK, self.state_track), Timing.from_time_str(splitted[0]))] = ButtonPress(button, int(splitted[1]), fx_data)
                except ValueError:
                    print(f'> > Warning: ignoring invalid button track {self.state_track}')


    def as_ksh(self, file=sys.stdout, metadata_only=False, jacket_idx=None, progress_bar=True, track_basename=None, preview_basename=None):
        # First print metadata.
        # TODO chokkaku, yomigana titles(?), background
        if jacket_idx is None:
            jacket_idx = str(self.difficulty.to_jacket_ifs_numer())

        if track_basename is None:
            track_basename = 'track.mp3'

        if preview_basename is None:
            preview_basename = 'preview.mp3'

        print(f'''title={self.get_metadata('title_name')}
artist={self.get_metadata('artist_name')}
effect={self.get_metadata('effected_by', True)}
jacket={jacket_idx}.png
illustrator={self.get_metadata('illustrator', True)}
difficulty={self.difficulty.to_ksh_name()}
level={self.get_metadata('difnum', True)}
t={self.bpm_string()}
m={track_basename}
mvol={self.get_metadata('volume')}
previewfile={preview_basename}
o=0
bg=desert
layer=arrow
po=0
plength=15000
pfiltergain=50
filtertype=peak
chokkakuautovol=0
chokkakuvol=50
ver=167''', file=file)

        if metadata_only:
            return

        print('--', file=file)

        holds = {}
        lasers = {LaserSide.LEFT: False, LaserSide.RIGHT: False}
        slam_status = {}
        current_timesig = self.events_timesig()[Timing(1, 1, 0)]

        measure_iter = range(self.end.measure)
        if progress_bar:
            from tqdm import tqdm
            measure_iter = tqdm(measure_iter, unit='measure', leave=False)

        for m in measure_iter:
            measure = m + 1

            # Laser range resets every measure in ksh.
            laser_range = {LaserSide.LEFT: 1, LaserSide.RIGHT: 1}

            now = Timing(measure, 1, 0)

            if now in self.events_timesig():
                current_timesig = self.events_timesig()[now]
                print(f'beat={current_timesig.top}/{current_timesig.bottom}', file=file)

            for b in range(current_timesig.top):
                # Vox beats are also 1-indexed.
                beat = b + 1

                for o in range(TICKS_PER_BEAT):
                    # However, vox offsets are 0-indexed.

                    now = Timing(measure, beat, o)

                    buffer = KshLineBuf()

                    if now.beat > 1 and now.offset > 0 and now in self.events_timesig():
                        raise KshConversionError('time signature change in the middle of a measure')

                    if now in self.events_bpm():
                        buffer.meta.append(f't={str(self.events_bpm()[now]).rstrip("0").rstrip(".").strip()}')

                    # Camera events.
                    for cam_param in CameraParam:
                        if now in self.events_spcontroller(cam_param):
                            the_change = self.events_spcontroller(cam_param)[now]
                            buffer.meta.append(f'{cam_param.to_ksh_name()}={int(the_change * cam_param.scaling_factor())}')

                    for i in list(Button):
                        if i in holds:
                            holds[i] -= 1
                            if holds[i] == 0:
                                del holds[i]
                            else:
                                buffer.buttons[i] = KshLineBuf.ButtonState.HOLD

                        if now in self.events_track(i.to_track_num()):
                            press: ButtonPress = self.events_track(i.to_track_num())[now]
                            if press.duration != 0 and i.is_fx():
                                letter = 'l' if i == Button.FX_L else 'r'
                                effect_string = press.effect[0].to_ksh_name(press.effect[1]) if press.effect is not None else \
                                    KshootEffect.choose_random().to_ksh_name(None)
                                buffer.meta.append(f'fx-{letter}={effect_string}')

                            if press.duration != 0:
                                buffer.buttons[i] = KshLineBuf.ButtonState.HOLD
                                holds[i] = press.duration
                            else:
                                buffer.buttons[i] = KshLineBuf.ButtonState.PRESS

                    for side in list(LaserSide):
                        laser = None
                        if now in self.events_track(side.to_track_num()) and side in slam_status:
                            raise KshConversionError('laser node created while trying to resolve slam')
                        elif now in self.events_track(side.to_track_num()):
                            laser = self.events_track(side.to_track_num())[now]
                        elif side in slam_status:
                            if slam_status[side][1] == 0:
                                laser = slam_status[side][0].end
                                del slam_status[side]
                            else:
                                buffer.lasers[side] = ':'
                                slam_status[side][1] -= 1
                                continue
                        else:
                            if lasers[side]:
                                buffer.lasers[side] = ':'
                            else:
                                buffer.lasers[side] = '-'
                            continue

                        if type(laser) is LaserSlam:
                            slam_status[side] = [laser, 3]
                            laser = laser.start

                        if laser.range != laser_range[side]:
                            buffer.meta.append(f'laserrange_{side.to_letter()}={laser.range}x')
                            laser_range[side] = laser.range

                        if laser.node_type == LaserCont.START:
                            lasers[side] = True
                        elif laser.node_type == LaserCont.END:
                            lasers[side] = False
                        buffer.lasers[side] = laser.position_ksh()

                    print(buffer.out(), file=file)


            print('--', file=file)

    @classmethod
    def from_file(cls, path):
        parser = Vox()

        file = open(path, 'r')
        parser.voxfile = file

        filename_array = os.path.basename(path).split('_')
        with open('data/music_db.xml', encoding='cp932') as db:
            try:
                parser.game_id = int(filename_array[0])
                parser.song_id = int(filename_array[1])
                parser.difficulty = Difficulty.from_letter(os.path.splitext(path)[0][-1])
                parser.difficulty_idx = os.path.splitext(path)[0][-2]
            except ValueError:
                raise VoxNameError(f'unable to parse file name "{path}"')

            tree = ElementTree.fromstring(db.read()).findall('''.//*[@id='{}']'''.format(parser.song_id))

            if len(tree) == 0:
                raise MetadataFindError(f'unable to find metadata for song {parser.song_id}')

            parser.metadata = tree[0]

        if len(ID_TO_AUDIO) > 0:
            if not parser.song_id in ID_TO_AUDIO:
                raise AudioFileFindError(f'unable to find audio file for song {parser.song_id}')
        # else we chose to skip audio.

        return parser

    def parse(self):
        line_no = 1
        for line in self.voxfile:
            line = line.strip()
            if line.startswith('//'):
                continue
            if line.startswith('#'):
                token_state = self.State.from_token(line.split('#')[1])
                if token_state is None:
                    continue
                if type(token_state) is tuple:
                    self.state = token_state[0]
                    self.state_track = int(token_state[1])
                else:
                    self.state = token_state
            elif line.startswith('define'):
                splitted = line.split('\t')

                # Sanity check.
                if splitted[0] != 'define':
                    raise ParserError('illegal define line in SOUND ID', self.voxfile, line_no)
                if len(splitted) != 3:
                    raise ParserError('illegal number of operands in define statement', self.voxfile, line_no)

                self.defines[splitted[1]] = splitted[2]
            elif self.state is not None:
                self.process_state(line)
            line_no += 1
        self.finalized = True

CASES = {
    'basic': 'data/vox_08_ifs/004_0781_alice_maestera_alstroemeria_records_5m.vox',
    'laser-range': 'data/vox_12_ifs/004_1138_newleaf_blackyooh_3e.vox',
    'time-signature': 'data/vox_01_ifs/001_0056_amanojaku_164_4i.vox',
    'early-version': 'data/vox_01_ifs/001_0001_albida_muryoku_1n.vox',
    'bpm': 'data/vox_03_ifs/002_0262_hanakagerou_minamotoya_1n.vox',
    'encoding': 'data/vox_02_ifs/001_0121_eclair_au_chocolat_kamome_1n.vox',
    'camera': 'data/vox_03_ifs/002_0250_crack_traxxxx_lite_show_magic_4i.vox',
    'diff-preview': 'data/vox_01_ifs/001_0026_gorilla_pinocchio_4i.vox',
    'slam-range': 'data/vox_06_ifs/003_0529_fks_nizikawa_3e.vox'
}

def copy_preview(vox, song_dir):
    split = os.path.splitext

    output_path = f'{song_dir}/preview.mp3'
    preview_path = f'{args.preview_dir}/{str(vox.game_id).zfill(3)}_{str(vox.song_id).zfill(4)}_pre.mp3'
    diff_preview_path = f'{split(preview_path)[0]}_{vox.diff_token()}{split(preview_path)[1]}'

    if os.path.exists(diff_preview_path):
        preview_path = diff_preview_path
        output_path = f'{split(output_path)[0]}_{vox.diff_token()}{split(output_path)[1]}'

    if os.path.exists(output_path):
        print(f'> Preview file "{output_path}" already exists.')
        return

    if os.path.exists(preview_path):
        print(f'> Copying preview to "{output_path}".')
        copyfile(preview_path, output_path)
    else:
        print('> No preview file found.')

    return os.path.basename(output_path)

argparser = argparse.ArgumentParser(description='Convert vox to ksh')
argparser.add_argument('-t', '--testcase')
argparser.add_argument('-m', '--metadata-only', action='store_true')
argparser.add_argument('-p', '--preview-only', action='store_true')
argparser.add_argument('-A', '--audio-dir', default='D:\\SDVX-Extract (V0)')
argparser.add_argument('-J', '--jacket-dir', default='D:\\SDVX-Extract (jk)')
argparser.add_argument('-P', '--preview-dir', default='D:\\SDVX-Extract (preview)')
argparser.add_argument('-n', '--no-media', action='store_true')
argparser.add_argument('-c', '--convert', action='store_true')
args = argparser.parse_args()

ID_TO_AUDIO = {}

if not args.no_media:
    print('Generating audio file mapping...')
    # Audio files should have a name starting with the ID followed by a space.
    for _, _, files in os.walk(args.audio_dir):
        for f in files:
            if os.path.basename(f) == 'jk':
                continue
            try:
                ID_TO_AUDIO[int(os.path.basename(f).split(' ')[0])] = f
            except ValueError as e:
                print(e)
    print(f'{len(ID_TO_AUDIO)} songs processed.')

if args.testcase:
    if not args.testcase in CASES:
        print('please specify a valid testcase', file=sys.stderr)
        print('valid testcases are:', file=sys.stderr)
        for c in CASES.keys():
            print('\t' + c, file=sys.stderr)
        exit(1)

if args.convert:
    # Create output directory.
    if not os.path.exists('out'):
        print(f'Creating output directory.')
        os.mkdir('out')

    VOX_ROOT = 'data'

    # Load source directory.
    for d in filter(lambda x: os.path.isdir(VOX_ROOT + '/' + x), os.listdir(VOX_ROOT)):
        vox_dir = VOX_ROOT + '/' + d
        for f in os.listdir(vox_dir):
            vox_path = f'{vox_dir}/{f}'
            if args.testcase and vox_path != CASES[args.testcase]:
                continue
            print(vox_path + ':')
            try:
                vox = Vox.from_file(vox_path)
            except (VoxNameError, MetadataFindError, AudioFileFindError) as e:
                print(f'> Skipping file "{vox_path}": {e}')
                continue

            print(f'> Processing {vox.song_id} "{vox.get_metadata("ascii")}" {vox.difficulty}.')

            # First try to parse the file.
            try:
                vox.parse()
            except Exception as e:
                print('> Parsing vox file failed with ' + str(e))
                continue

            game_dir = f'out/{str(vox.game_id).zfill(3)}'
            if not os.path.isdir(game_dir):
                print(f'> Making game directory "{game_dir}".')
                os.mkdir(game_dir)
            song_dir = f'{game_dir}/{vox.get_metadata("ascii")}'
            if not os.path.isdir(song_dir):
                print(f'> Creating song directory "{song_dir}".')
                os.mkdir(song_dir)

            preview_basename = copy_preview(vox, song_dir)
            if args.preview_only:
                continue

            fallback_jacket_diff_idx = None

            using_difficulty_audio = False

            if not args.no_media:

                target_audio_path = song_dir + '/track.mp3'

                src_audio_path = args.audio_dir + '/' + ID_TO_AUDIO[vox.song_id]

                if vox.difficulty == Difficulty.INFINITE:
                    src_audio_path_diff = f'{splitx(src_audio_path)[0]} [INF]{splitx(src_audio_path)[1]}'
                    print(src_audio_path_diff)
                    if os.path.exists(src_audio_path_diff):
                        print(f'> Found difficulty-specific audio "{src_audio_path_diff}".')
                        src_audio_path = src_audio_path_diff
                        target_audio_path = f'{splitx(target_audio_path)[0]}_inf{splitx(target_audio_path)[1]}'
                        using_difficulty_audio = True

                if not os.path.exists(target_audio_path):
                    print(f'> Copying audio file {src_audio_path} to song directory.')
                    copyfile(src_audio_path, target_audio_path)
                else:
                    print(f'> Audio file "{target_audio_path}" already exists.')

                src_jacket_basename = f'jk_{str(vox.game_id).zfill(3)}_{str(vox.song_id).zfill(4)}_{vox.difficulty.to_jacket_ifs_numer()}_b'
                src_jacket_path = args.jacket_dir + '/' + src_jacket_basename + '_ifs/tex/' + src_jacket_basename + '.png'

                if os.path.exists(src_jacket_path):
                    target_jacket_path = f'{song_dir}/{str(vox.difficulty.to_jacket_ifs_numer())}.png'
                    print(f'> Jacket image file found at "{src_jacket_path}". Copying to "{target_jacket_path}".')
                    copyfile(src_jacket_path, target_jacket_path)
                else:
                    print(f'> Could not find jacket image file. Checking easier diffs.')
                    fallback_jacket_diff_idx = vox.difficulty.to_jacket_ifs_numer() - 1
                    while True:
                        if fallback_jacket_diff_idx < 0:
                            print('> No jackets found for easier difficulties either. Leaving jacket blank.')
                            fallback_jacket_diff_idx = ''
                            break

                        easier_jacket_path = f'{song_dir}/{fallback_jacket_diff_idx}.png'
                        if os.path.exists(easier_jacket_path):
                            # We found the diff number with the jacket.
                            print(f'> Using jacket "{easier_jacket_path}".')
                            break
                        fallback_jacket_diff_idx -= 1

            chart_path = f'{song_dir}/{vox.difficulty.to_xml_name()}.ksh'
            print(f'> Writing KSH data to "{chart_path}".')
            with open(chart_path, "w+", encoding='utf-8') as ksh_file:
                try:
                    vox.as_ksh(file=ksh_file,
                               jacket_idx=str(fallback_jacket_diff_idx) if fallback_jacket_diff_idx is not None else None,
                               track_basename='track_inf.mp3' if using_difficulty_audio else None,
                               preview_basename=preview_basename)
                except Exception as e:
                    print(f'Outputting to KSH failed with {e}. Traceback:\n{traceback.format_exc()}')
                    continue
                print('> Success!')
    exit(0)

print('Please specify something to do.')
exit(1)