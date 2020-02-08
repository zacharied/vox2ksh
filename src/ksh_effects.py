from enum import Enum

from typing import Optional, Union, Callable

# TODO In effect init, check whether main_param is a tuple and ignore it if so.

class KshEffectKind(Enum):
    RETRIGGER = 'Retrigger'
    GATE = 'Gate'
    FLANGER = 'Flanger'
    BITCRUSHER = 'BitCrusher'
    PHASER = 'Phaser'
    WOBBLE = 'Wobble'
    PITCHSHIFT = 'PitchShift'
    TAPESTOP = 'TapeStop'
    ECHO = 'Echo'
    SIDECHAIN = 'SideChain'

class KshEffect:
    effect: KshEffectKind
    main_param: Optional[str]
    params: {str}

    def __init__(self, kind, main_param=None):
        self.effect = kind
        self.main_param = main_param
        self.params: {str} = {}

    def fx_change(self, index, duration=0):
        if self.effect == KshEffectKind.TAPESTOP:
            # Math lol
            extra = f';{int(2500 / (duration + 10))}'
        else:
            extra = f';{self.main_param}' if self.main_param is not None else ''
        return f'{index}{extra}'

    def define_line(self, index):
        # If this is not set, the effect will ALWAYS be triggered on an FX hold regardless of what effect is actually
        #  assigned to that.
        if 'mix' in self.params:
            self.params['mix'] = f'0%>{self.params["mix"]}'
        param_str = ''
        for k, v in self.params.items():
            param_str += f';{k}={v}'
        return f'#define_fx {index} type={self.effect.value}{param_str}'

    @classmethod
    def operate(cls, val: Union[str, tuple], operation: Callable):
        if type(val) is str:
            return operation(val)
        elif type(val) is tuple:
            return (cls.operate(val[0], operation), cls.operate(val[1], operation))

    @classmethod
    def division(cls, numerator: int, val: Union[str, tuple]):
        """
        Helper method for creating FX definitions.
        Returns a string in the form of '1/val'.
        """
        if type(val) is str:
            return f'{numerator}/{int(float(val))}'
        elif type(val) is tuple:
            return f'{cls.division(numerator, val[0])}-{cls.division(numerator, val[1])}'

    @classmethod
    def percent(cls, val: Union[str, tuple], is_decimal=True):
        """
        Helper method for creating FX definitions.
        Returns a string containing val as a percentage, multiplied by 100 if `is_decimal` is true.
        """
        if type(val) is str:
            val = float(val) * 100 if is_decimal else float(val)
            return str(int(val)) + '%'
        elif type(val) is tuple:
            return f'{cls.percent(val[0])}-{cls.percent(val[1])}'

    @classmethod
    def suffix(cls, val: Union[str, tuple], suffix: str):
        if type(val) is str:
            return val + suffix
        elif type(val) is tuple:
            return f'{cls.suffix(val[0], suffix)}-{cls.suffix(val[1], suffix)}'

class RetriggerEffect(KshEffect):
    def __init__(self, division: str, mix, update_period: str, feedback, unknown1, rate):
        super(RetriggerEffect, self).__init__(KshEffectKind.RETRIGGER)
        wavelength = str(int((4 / float(update_period)) * float(division)))

        if float(feedback) != 1.0:
            self.effect = KshEffectKind.ECHO
            self.params['feedbackLevel'] = self.percent(feedback)

        if float(update_period) > 0:
            self.main_param = wavelength
            self.params['waveLength'] = self.division(1, wavelength)
            self.params['updatePeriod'] = self.division(1, update_period)
            if self.effect != KshEffectKind.ECHO:
                # Echo does not take 'rate'.
                self.params['rate'] = self.percent(str(1.0 - float(rate)))
        else:
            # TODO This may or may not be correct.
            self.main_param = str(int(float(wavelength) * 16))
            self.params['waveLength'] = f'1/{self.main_param}'
            self.params['updatePeriod'] = '1/18'

        self.params['mix'] = self.percent(mix, is_decimal=False)

class UpdateableRetriggerEffect(RetriggerEffect):
    def __init__(self, division: str, mix, update_period: str, feedback, unknown1, rate, unknown2):
        super(UpdateableRetriggerEffect, self).__init__(division, mix, update_period, feedback, unknown1, rate)
        self.params['updateTrigger'] = 'off>on'

class GateEffect(KshEffect):
    def __init__(self, mix, division1: str, division2: str):
        # TODO Outside of this formula, I'm not entirely sure how division1 and division2 interact with each other.
        super(GateEffect, self).__init__(KshEffectKind.GATE)
        wavelength = str(int((2 / float(division2)) * float(division1)))
        self.main_param = wavelength
        self.params['waveLength'] = f'1/{wavelength}'
        self.params['mix'] = self.percent(mix, is_decimal=False)

class WobbleEffect(KshEffect):
    def __init__(self, unknown1, unknown2, mix, low_freq, high_freq, wavelength, resonance):
        super(WobbleEffect, self).__init__(KshEffectKind.WOBBLE)
        # TODO Generify this reference to wavelength to make it work with tuples.
        self.main_param = None if type(wavelength) is tuple else str(int(float(wavelength) * 4))

        # Perform the multiplication on our wavelength through a helper in the event that it's a tuple.
        self.params['waveLength'] = self.division(1, str(self.operate(wavelength, lambda n: int(float(n) * 4))))
        self.params['loFreq'] = self.suffix(str(int(float(low_freq))), 'Hz')
        self.params['hiFreq'] = self.suffix(str(int(float(high_freq))), 'Hz')
        self.params['Q'] = float(resonance)
        self.params['mix'] = self.percent(mix, is_decimal=False)

class LowpassEffect(KshEffect):
    def __init__(self, unknown1, unknown2, freq, unknown3):
        super(LowpassEffect, self).__init__(KshEffectKind.WOBBLE)
        self.main_param = 1
        # TOOD Check unknowns.
        freq = str(int(float(freq)))
        self.params['loFreq'] = self.suffix(str(freq), 'Hz')
        self.params['hiFreq'] = self.suffix(str(freq), 'Hz')

class SidechainEffect(KshEffect):
    def __init__(self, mix, period, hold_time, attack_time, release_time):
        super(SidechainEffect, self).__init__(KshEffectKind.SIDECHAIN)
        self.main_param = None if type(period) is tuple else int(float(period) * 2)
        self.params['period'] = self.division(1, str(int(float(period) * 2)))
        self.params['holdTime'] = self.suffix(str(int(hold_time)), 'ms')
        self.params['attackTime'] = self.suffix(str(int(attack_time)), 'ms')
        self.params['releaseTime'] = self.suffix(str(int(release_time)), 'ms')
        # Sidechain does not take a mix parameter.

class BitcrusherEffect(KshEffect):
    def __init__(self, mix, samples):
        super(BitcrusherEffect, self).__init__(KshEffectKind.BITCRUSHER)
        self.main_param = samples
        self.params['reduction'] = self.suffix(samples, 'samples')
        self.params['mix'] = self.percent(mix, is_decimal=False)

class TapestopEffect(KshEffect):
    def __init__(self, mix, unknown1, unknown2):
        super(TapestopEffect, self).__init__(KshEffectKind.TAPESTOP)
        # This will be overridden per note based on the note's length.
        self.main_param = '69'
        self.params['speed'] = 69
        self.params['mix'] = self.percent(mix, is_decimal=False)

class PhaserEffect(KshEffect):
    def __init__(self, mix, period, feedback, stereo_width, high_cut_gain):
        super(PhaserEffect, self).__init__(KshEffectKind.PHASER)
        self.main_param = str(int(float(period)))
        self.params['period'] = self.division(1, period)
        self.params['feedback'] = self.percent(feedback)
        self.params['stereoWidth'] = self.percent(stereo_width, is_decimal=False)
        self.params['hiCutGain'] = self.suffix(self.operate(high_cut_gain, lambda n: str(-int(float(n)))), 'dB')
        self.params['mix'] = self.percent(mix, is_decimal=False)

class PitchshiftEffect(KshEffect):
    def __init__(self, mix, samples):
        super(PitchshiftEffect, self).__init__(KshEffectKind.BITCRUSHER)
        self.main_param = samples
        self.params['reduction'] = self.suffix(samples, 'samples')
        self.params['mix'] = self.percent(mix, is_decimal=False)

class FlangerEffect(KshEffect):
    def __init__(self, mix, samples, depth, period):
        super(FlangerEffect, self).__init__(KshEffectKind.FLANGER)
        self.main_param = str(int(float(samples)) / 10)
        self.params['depth'] = self.suffix(depth, 'samples')
        self.params['period'] = self.division(1, period)
        self.params['mix'] = self.percent(mix, is_decimal=False)
