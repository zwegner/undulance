import math
import random
import threading

class Context:
    def __init__(self):
        self.syms = {}
    def load(self, name):
        return self.syms.get(name, 0)
    def store(self, name, value):
        self.syms[name] = value
        return self.syms[name]

def fixup(arg):
    if isinstance(arg, list):
        return [fixup(a) for a in arg]
    if isinstance(arg, (float, int)):
        arg = Const(arg)
    if isinstance(arg, str):
        arg = Load(arg)
    return arg

def operator(*params):
    def decorate(cls):
        def __init__(self, *args):
            for p, a in zip(params, args):
                if p.startswith('!'):
                    p = p[1:]
                else:
                    a = fixup(a)
                setattr(self, p, a)
                if hasattr(a, 'eval'):
                    setattr(self, '%s_eval' % p, a.eval)
            if hasattr(self, 'setup'):
                self.setup()
        cls.__init__ = __init__
        if not hasattr(cls, '__str__'):
            def __str__(self):
                return '%s(%s)' % (self.__class__.__name__,
                        ', '.join(str(getattr(self, p)) for p in params))
            cls.__str__ = __str__
        return cls
    return decorate

class Node:
    def __int__(self):
        return Int(self)

@operator('!value')
class Const(Node):
    def eval(self, ctx):
        return self.value
    def __str__(self):
        return '%s' % self.value

@operator('value')
class Int(Node):
    def eval(self, ctx):
        return int(self.value_eval(ctx))

@operator('!name')
class Load(Node):
    def eval(self, ctx):
        return ctx.load(self.name)

@operator('!name', 'value')
class Store(Node):
    def eval(self, ctx):
        return ctx.store(self.name, self.value_eval(ctx))

@operator('lhs', 'rhs')
class Binop(Node):
    def __str__(self):
        return '(%s %s %s)' % (self.lhs, self.__class__.op, self.rhs)

def binop(op):
    def deco(x):
        x.op = op
        return x
    return deco

@binop('+')
class Add(Binop):
    def eval(self, ctx):
        return self.lhs_eval(ctx) + self.rhs_eval(ctx)

@binop('-')
class Sub(Binop):
    def eval(self, ctx):
        return self.lhs_eval(ctx) - self.rhs_eval(ctx)

@binop('*')
class Mul(Binop):
    def eval(self, ctx):
        return self.lhs_eval(ctx) * self.rhs_eval(ctx)

@binop('/')
class Div(Binop):
    def eval(self, ctx):
        return self.lhs_eval(ctx) / self.rhs_eval(ctx)

@binop('%')
class Mod(Binop):
    def eval(self, ctx):
        return self.lhs_eval(ctx) % self.rhs_eval(ctx)

# Add in operator overloading to Node class. Must be done after the child classes
# are instantiated... weird.

ops = {
    'add': Add,
    'sub': Sub,
    'mul': Mul,
    'truediv': Div,
    'mod': Mod,
}
for name, cls in ops.items():
    # ugh, make sure there's a new scope so the right names get captured
    def add(name, cls):
        def binop(self, rhs):
            return cls(self, rhs)
        def rbinop(self, lhs):
            return cls(lhs, self)
        setattr(Node, '__%s__' % name, binop)
        setattr(Node, '__r%s__' % name, rbinop)
    add(name, cls)

@operator('freq')
class Osc(Node):
    def setup(self):
        self.phase = 0
        self.last_freq = None
    def eval(self, ctx):
        freq = self.freq_eval(ctx)
        if freq != self.last_freq:
            self.ratio = freq / ctx.sample_rate
            self.last_freq = freq
        self.phase = ctx.sample * self.ratio
        self.phase -= int(self.phase)
        return self.eval_wave(self.phase)
    def __str__(self):
        return '%s(%s)' % (self.__class__.__name__, self.freq)

class Sine(Osc):
    def eval_wave(self, phase):
        return math.sin(phase * 2 * math.pi)

class Square(Osc):
    def eval_wave(self, phase):
        return 1 if phase > 0.5 else -1

@operator('freq', 'pulse_width')
class Pulse(Osc):
    def eval_wave(self, phase):
        return 1 if phase > self.pulse_width_eval(ctx) else -1

class SawUp(Osc):
    def eval_wave(self, phase):
        return 2 * phase - 1

class SawDown(Osc):
    def eval_wave(self, phase):
        return -2 * phase + 1

class Tri(Osc):
    def eval_wave(self, phase):
        return 4 * phase - 1 if phase < 0.5 else -4 * phase + 3

@operator()
class Noise(Node):
    def eval(self, ctx):
        return 2 * random.random() - 1

@operator('time')
class TimeToSamples(Node):
    def eval(self, ctx):
        return (self.time_eval(ctx) * ctx.sample_rate)

@operator('input', 'gate')
class ExpEnvelope(Node):
    def setup(self):
        self.last_gate = 0
        self.current = 0
    def eval(self, ctx):
        gate = self.gate_eval(ctx) > 0
        trigger = gate and not self.last_gate
        self.last_gate = gate
        if trigger:
            self.current = 1
        self.current *= .9999
        return self.current * self.input_eval(ctx)

@operator('input', 'time', 'gate')
class Envelope(Node):
    def setup(self):
        self.last_gate = 0
        self.current = 0
        self.ratio = 0
    def eval(self, ctx):
        gate = self.gate_eval(ctx) > 0
        trigger = gate and not self.last_gate
        self.last_gate = gate
        if trigger:
            self.current = 1
            self.ratio = 1 / (self.time_eval(ctx) * ctx.sample_rate)
        self.current = max(0, self.current - self.ratio)
        return self.current * self.input_eval(ctx)

def EnvelopeBeat(input, time, beat):
    return Envelope(input, time, Trigger(beat))

def ExpEnvelopeBeat(input, beat):
    return ExpEnvelope(input, Trigger(beat))

@operator('note')
class Diatonic(Node):
    half_step = 2 ** (1 / 12)
    def setup(self):
        self.last_note = None
    def eval(self, ctx):
        note = self.note_eval(ctx)
        if note != self.last_note:
            self.value = 256 * Diatonic.half_step ** (note - 40)
            self.last_note = note
        return self.value

@operator('note', 'scale')
class Scale(Node):
    def eval(self, ctx):
        note = int(self.note_eval(ctx))
        while not self.scale_eval(ctx)[note % 12]:
            note -= 1
        return note

major_notes = [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1]
#major_notes = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
scales = {root: (major_notes * 2)[root:root+12] for root in range(12)}
@operator('root')
class MajorScale(Node):
    def eval(self, ctx):
        return scales[self.root_eval(ctx)]

@operator('bpm')
class Beat(Node):
    def eval(self, ctx):
        return ctx.sample * self.bpm_eval(ctx) / (ctx.sample_rate * 60)

@operator('beat')
class Rhythm(Node):
    def eval(self, ctx):
        return self.beat_eval(ctx)

@operator('trigger', 'signal')
class Sample(Node):
    def setup(self):
        self.sampled = 0
    def eval(self, ctx):
        if self.trigger_eval(ctx):
            self.sampled = self.signal_eval(ctx)
        return self.sampled

@operator('beat')
class Trigger(Node):
    def setup(self):
        self.last_beat = -1
    def eval(self, ctx):
        beat = int(self.beat_eval(ctx))
        trigger = beat != self.last_beat
        self.last_beat = beat
        return trigger

@operator('beat', 'args')
class Switcher(Node):
    def eval(self, ctx):
        return self.args[int(self.beat_eval(ctx)) % len(self.args)].eval(ctx)

#class Historic:

@operator('value1', 'value2', 'ratio')
class Interpolate(Node):
    def eval(self, ctx):
        ratio = self.ratio_eval(ctx) 
        return self.value1_eval(ctx) * ratio + self.value2_eval(ctx) * (1 - ratio)

ctx = Context()
ctx.sample_rate = sample_rate

@operator('expr', '!args')
class FunctionCall(Node):
    def eval(self, ctx):
        for k, v in self.args.items():
            ctx.store(k, v)
        return self.expr.eval(ctx)
    def __str__(self):
        return 'f(%s) { %s }' % (', '.join(self.args), self.expr)

class MIDIThread(threading.Thread):
    def __init__(self, shim):
        super().__init__()
        self.shim = shim
    def run(self):
        import mido
        notes = {}
        with mido.open_input(self.shim.device) as input:
            for msg in input:
                if msg.type == 'note_on':
                    notes[msg.note] = msg.velocity
                elif msg.type == 'note_off':
                    del notes[msg.note]
                self.shim.set_notes(notes)

@operator('!device', '!value')
class MIDIShim(Node):
    def setup(self):
        self.eq = Const(0)
        self.thread = MIDIThread(self)
        self.thread.daemon = True
        self.thread.start()
    def eval(self, ctx):
        return self.eq.eval(ctx)
    def set_notes(self, notes):
        self.eq = sum((FunctionCall(self.value, {'note': k, 'velocity': v})
            for k, v in notes.items()), Const(0))

beat = Beat(120)
section = Switcher(beat / 4, [1, 3, 6, 8])
bs = beat * section
eq = Const(0)
#eq += EnvelopeBeat(SawUp(Diatonic(section + Switcher(beat * section, [30, 32, 39, 42]))), beat * section)
eq = EnvelopeBeat(SawUp(Diatonic(Scale(Sample(Trigger(bs), SawDown(Const(200) / (section + .5)) * 13 + 40), MajorScale(0)))), section / 10, bs)
#eq += ExpEnvelopeBeat(Square(96), beat)
#eq += ExpEnvelopeBeat(SawUp(256), beat * section)
#eq += ExpEnvelopeBeat(Square(Diatonic(Sample(Trigger(beat*section), SawUp(beat*2 + 490)) * 4 + 30)), beat)
#eq += ExpEnvelopeBeat(Noise(), beat / 4)
