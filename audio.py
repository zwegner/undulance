import copy
import math
import random
import threading

class Context:
    def __init__(self):
        self.syms = {}
        self.value_cache = {}
    def load(self, name):
        return self.syms.get(name, 0)
    def store(self, name, value):
        self.syms[name] = value
        return value
    def reset(self):
        # XXX Clear cache. This is kinda wonky...
        self.value_cache = {}

def fixup(arg):
    if isinstance(arg, list):
        return [fixup(a) for a in arg]
    if isinstance(arg, (float, int)):
        arg = Const(arg)
    if isinstance(arg, str):
        arg = Load(arg)
    return arg

def operator(*params, **kwparams):
    def decorate(cls):
        def __init__(self, *args, **kwargs):
            for p, a in zip(params, args):
                if p.startswith('!'):
                    p = p[1:]
                else:
                    a = fixup(a)
                setattr(self, p, a)

            self.last_value = None

            for p in kwparams:
                setattr(self, p, kwargs[p] if p in kwargs else kwparams[p])
            assert all(a in kwparams for a in kwargs)

            if hasattr(self, 'setup'):
                self.setup()
        cls.__init__ = __init__

        # Redirect eval() calls through a caching layer, so that each node
        # gets evaluated at most once per sample
        if not hasattr(cls, 'base_eval'):
            cls.base_eval, cls.eval = cls.eval, cls.eval_cached

        if not hasattr(cls, '__str__'):
            def __str__(self):
                return '%s(%s)' % (self.__class__.__name__,
                        ', '.join(str(getattr(self, p)) for p in params))
            cls.__str__ = __str__
        return cls
    return decorate

def operator_fn(*params, **kwparams):
    def classify(fn):
        @operator(*params, **kwparams)
        class Op(Node):
            eval = fn
        return Op
    return classify

class Node:
    def __int__(self):
        return Int(self)
    def eval_cached(self, ctx):
        if self not in ctx.value_cache:
            ctx.value_cache[self] = self.base_eval(ctx)
        return ctx.value_cache[self]
    def eval_changed(self, ctx):
        value = self.eval(ctx)
        result = [value, value != self.last_value]
        self.last_value = value
        return result

@operator('!value')
class Const(Node):
    def eval(self, ctx):
        return self.value
    def __str__(self):
        return '%s' % self.value

@operator_fn('value')
def Int(self, ctx):
    return int(self.value.eval(ctx))

@operator_fn('value')
def Bool(self, ctx):
    return bool(self.value.eval(ctx))

@operator_fn('!name')
def Load(self, ctx):
    return ctx.load(self.name)

@operator_fn('!name', 'value')
def Store(self, ctx):
    return ctx.store(self.name, self.value.eval(ctx))

class Binop(Node):
    def __str__(self):
        return '(%s %s %s)' % (self.lhs, self.__class__.op, self.rhs)

def binop(op):
    def deco(x):
        x = operator('lhs', 'rhs')(x)
        x.op = op
        return x
    return deco

@binop('+')
class Add(Binop):
    def eval(self, ctx):
        return self.lhs.eval(ctx) + self.rhs.eval(ctx)

@binop('-')
class Sub(Binop):
    def eval(self, ctx):
        return self.lhs.eval(ctx) - self.rhs.eval(ctx)

@binop('*')
class Mul(Binop):
    def eval(self, ctx):
        return self.lhs.eval(ctx) * self.rhs.eval(ctx)

@binop('/')
class Div(Binop):
    def eval(self, ctx):
        return self.lhs.eval(ctx) / self.rhs.eval(ctx)

@binop('%')
class Mod(Binop):
    def eval(self, ctx):
        return self.lhs.eval(ctx) % self.rhs.eval(ctx)

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

@operator('freq', sync=None)
class Osc(Node):
    def setup(self):
        self.phase = 0
        self.last_sync_phase = 1
    def eval(self, ctx):
        [freq, changed] = self.freq.eval_changed(ctx)
        if changed:
            self.ratio = freq / ctx.load('sample_rate')
        if self.sync:
            self.sync.eval(ctx)
            if self.sync.phase < self.last_sync_phase:
                self.phase = 0
            self.last_sync_phase = self.sync.phase
        self.phase += self.ratio
        self.phase -= int(self.phase)
        return self.eval_wave(ctx, self.phase)
    def __str__(self):
        return '%s(%s)' % (self.__class__.__name__, self.freq)

class Sine(Osc):
    def eval_wave(self, ctx, phase):
        return math.sin(phase * 2 * math.pi)

class Cosine(Osc):
    def eval_wave(self, ctx, phase):
        return math.cos(phase * 2 * math.pi)

class Square(Osc):
    def eval_wave(self, ctx, phase):
        return 1 if phase > 0.5 else -1

@operator('freq', 'pulse_width', sync=None)
class Pulse(Osc):
    def eval_wave(self, ctx, phase):
        return 1 if phase > self.pulse_width.eval(ctx) else -1

class SawUp(Osc):
    def eval_wave(self, ctx, phase):
        return 2 * phase - 1

class SawDown(Osc):
    def eval_wave(self, ctx, phase):
        return -2 * phase + 1

class Tri(Osc):
    def eval_wave(self, ctx, phase):
        return 4 * phase - 1 if phase < 0.5 else -4 * phase + 3

@operator_fn()
def Noise(self, ctx):
    return 2 * random.random() - 1

# Equations for these filters from https://github.com/graue/luasynth
@operator('input', 'cutoff', 'resonance')
class Filter(Node):
    def setup(self):
        self.hist_x = HistBuffer()
        self.hist_y = HistBuffer()
    def eval(self, ctx):
        [cutoff, cutoff_changed] = self.cutoff.eval_changed(ctx)
        [resonance, resonance_changed] = self.resonance.eval_changed(ctx)
        # Update coefficients if needed
        if cutoff_changed or resonance_changed:
            w0 = 2 * math.pi * (cutoff / ctx.load('sample_rate'))
            sin_w0 = math.sin(w0)
            cos_w0 = math.cos(w0)
            alpha = sin_w0 / (2 * self.resonance.eval(ctx))

            a = [1 + alpha, -2 * cos_w0, 1 - alpha]
            b = self.get_coeffs(sin_w0, cos_w0)
            self.c1 = b[0] / a[0]
            self.c2 = b[1] / a[0]
            self.c3 = b[2] / a[0]
            self.c4 = a[1] / a[0]
            self.c5 = a[2] / a[0]

        # Evaluate the filter recurrence relation
        self.hist_x.push_value(self.input.eval(ctx))
        y0 = (self.c1 * self.hist_x[0] +
            self.c2 * self.hist_x[1] +
            self.c3 * self.hist_x[2] -
            self.c4 * self.hist_y[0] -
            self.c5 * self.hist_y[1])
        self.hist_y.push_value(y0)
        return y0

class LowpassFilter(Filter):
    def get_coeffs(self, sin_w0, cos_w0):
        return [(1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2]

class HighpassFilter(Filter):
    def get_coeffs(self, sin_w0, cos_w0):
        return [(1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2]

class BandpassFilter(Filter):
    def get_coeffs(self, sin_w0, cos_w0):
        return [sin_w0 / 2, 0, -sin_w0 / 2]

class NotchFilter(Filter):
    def get_coeffs(self, sin_w0, cos_w0):
        return [1, -2 * cos_w0, 1]

@operator('input', 'cutoff')
class AllpassFilter(Filter):
    def setup(self):
        self.last = 0
    def eval(self, ctx):
        delay = self.cutoff.eval(ctx) / ctx.load('sample_rate')
        c = (1 - delay) / (1 + delay)

        v = self.input.eval(ctx)
        y0 = -c * v + self.last
        self.last = c * y0 + v
        return y0

@operator('value', 'cutoff')
class SoftSaturation(Node):
    def eval_cutoff(self, value, cutoff):
        if value < cutoff:
            return value
        elif value < 1:
            diff = (value - cutoff)
            return cutoff + diff / (1 + (diff / (1 - cutoff)) ** 2)
        else:
            return (cutoff + 1) / 2
    def eval(self, ctx):
        value = self.value.eval(ctx)
        cutoff = self.cutoff.eval(ctx)
        if value < 0:
            return -self.eval_cutoff(-value, cutoff)
        return self.eval_cutoff(value, cutoff)

class HardSaturation(SoftSaturation):
    def eval_cutoff(self, value, cutoff):
        return min(value, cutoff)

@operator('input', 'gate')
class ExpEnvelope(Node):
    def setup(self):
        self.gate = Bool(self.gate)
        self.current = 0
    def eval(self, ctx):
        [gate, changed] = self.gate.eval_changed(ctx)
        if gate and changed:
            self.current = 1
        self.current *= .9999
        return self.current * self.input.eval(ctx)

@operator('input', 'time', 'gate')
class Envelope(Node):
    def setup(self):
        self.gate = Bool(self.gate)
        self.current = 0
        self.ratio = 0
    def eval(self, ctx):
        [gate, changed] = self.gate.eval_changed(ctx)
        if gate and changed:
            self.current = 1
            self.ratio = 1 / (self.time.eval(ctx) * ctx.load('sample_rate'))
        self.current = max(0, self.current - self.ratio)
        return self.current * self.input.eval(ctx)

def EnvelopeBeat(input, time, beat):
    return Envelope(input, time, Trigger(beat))

def ExpEnvelopeBeat(input, beat):
    return ExpEnvelope(input, Trigger(beat))

@operator('note')
class Diatonic(Node):
    half_step = 2 ** (1 / 12)
    def eval(self, ctx):
        [note, changed] = self.note.eval_changed(ctx)
        if changed:
            self.value = 440 * Diatonic.half_step ** (note - 69)
        return self.value

@operator_fn('note', 'scale')
def Scale(self, ctx):
    note = int(self.note.eval(ctx))
    while not self.scale.eval(ctx)[note % 12]:
        note -= 1
    return note

major_notes = [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1] * 2
scales = {root: major_notes[root:root+12] for root in range(12)}
@operator_fn('root')
def MajorScale(self, ctx):
    return scales[self.root.eval(ctx)]

@operator_fn('bpm')
def Beat(self, ctx):
    return ctx.load('sample') * self.bpm.eval(ctx) / (ctx.load('sample_rate') * 60)

@operator('!notes', 'beat')
class Rhythm(Node):
    def setup(self):
        notes = []
        beat = 0
        for n in self.notes:
            beat += n
            notes.append(beat - 1)
        self.notes = notes
    def eval(self, ctx):
        beat = int(self.beat.eval(ctx))
        n = len(self.notes)
        return (beat // n * n) + self.notes[beat % n]

@operator('trigger', 'signal')
class Sample(Node):
    def setup(self):
        self.sampled = 0
    def eval(self, ctx):
        if self.trigger.eval(ctx):
            self.sampled = self.signal.eval(ctx)
        return self.sampled

@operator('target', 'step', 'trigger')
class Glissando(Node):
    def setup(self):
        self.value = None
    def eval(self, ctx):
        if self.value is None:
            self.value = self.target.eval(ctx)
        if self.trigger.eval(ctx):
            step = abs(self.step.eval(ctx))
            target = self.target.eval(ctx)
            # To prevent instability, only add the step if it gets us closer
            if abs(self.value - target) > step / 2:
                self.value += step if self.value < target else -step
        return self.value

@operator('beat')
class Trigger(Node):
    def setup(self):
        self.beat = Int(self.beat)
    def eval(self, ctx):
        [beat, changed] = self.beat.eval_changed(ctx)
        return changed

@operator_fn('beat', 'args')
def Switcher(self, ctx):
    return self.args[int(self.beat.eval(ctx)) % len(self.args)].eval(ctx)

class HistBuffer:
    def __init__(self):
        self.buffer = [0]
        self.current_index = 0
    def push_value(self, value):
        self.current_index = (self.current_index + 1) % len(self.buffer)
        self.buffer[self.current_index] = value
    def __getitem__(self, index):
        if index >= len(self.buffer):
            self.buffer = (self.buffer[:self.current_index + 1] +
                [0] * (index - len(self.buffer) + 1) +
                self.buffer[self.current_index + 1:])
        index = (self.current_index - index) % len(self.buffer)
        return self.buffer[index]

@operator('value', 'index')
class Historic(Node):
    def setup(self):
        self.hist_buffer = HistBuffer()
    def eval(self, ctx):
        self.hist_buffer.push_value(self.value.eval(ctx))
        return self.hist_buffer[int(self.index.eval(ctx))]

temp_id = 0
def Delay(value, time, drywet, feedback):
    global temp_id
    temp_id += 1
    temp = '__delay%s' % temp_id
    delayed = Store(temp, Historic(value + feedback * Load(temp),
        (time * Load('sample_rate'))))
    return Interpolate(value, delayed, drywet)

def Phaser(value, cutoff, drywet, stages=12):
    filtered = value
    for i in range(stages):
        filtered = AllpassFilter(filtered, cutoff)
    return Interpolate(value, filtered, drywet)

def interpolate(value1, value2, ratio):
    return value1 * (1 - ratio) + value2 * ratio

@operator_fn('value1', 'value2', 'ratio')
def Interpolate(self, ctx):
    return interpolate(self.value1.eval(ctx), self.value2.eval(ctx),
        self.ratio.eval(ctx))

@operator_fn('value', 'folds', 'gain', 'base')
def WaveFolder(self, ctx):
    folds = int(self.folds.eval(ctx))
    base = self.base.eval(ctx)
    value = (self.value.eval(ctx) - base) * self.gain.eval(ctx) * folds
    for i in range(folds):
        if value > 1:
            value = 2 - value
        elif value < -1:
            value = -2 - value
    return value + base

def Chord(notes, base, fn):
    return sum(FunctionCall(fn, {'note': base + k}) for k in notes)

@operator_fn('value', 'position')
def Pan(self, ctx):
    value = self.value.eval(ctx)
    pos = (self.position.eval(ctx) + 1) / 2
    if ctx.load('channel'):
        pos = 1 - pos
    return 2 * value * pos

@operator('value', 'pos_x', 'pos_y')
class Pan2D(Node):
    def setup(self):
        self.hist_buffer = HistBuffer()
    def eval(self, ctx):
        self.hist_buffer.push_value(self.value.eval(ctx))

        # Very basic model of human hearing: figure out a distance from each
        # virtual ear (i.e. stereo channel), and compute a delay time from this
        # based on an arbitrary-but-seems-to-sound-OK speed of sound figure.
        pos_x = self.pos_x.eval(ctx)
        pos_y = self.pos_y.eval(ctx)

        channel = ctx.load('channel')
        if channel:
            pos_x = -pos_x
        pos_x -= .1 # Distance from ear to center of head

        dist = math.hypot(pos_x, pos_y)
        delay = int(dist * ctx.load('sample_rate') / 50)

        # Also very rough attenuation based on traveling through a virtual
        # human head. Dumb geometric approximation--basically increase linearly
        # from .5 attenuation on the other side of the head to 1 in the center.
        angle = abs(math.atan2(pos_y, pos_x))
        att = 1 - max(0, angle / (2 * math.pi))

        return att * self.hist_buffer[delay]

def Chorus(value, rate, base=.01, diff=.003):
    return Delay(value, base + diff * Sine(rate) * Interpolate(1, -1, 'channel'), .5, 0)

@operator('expr', '!args')
class FunctionCall(Node):
    def setup(self):
        self.expr = copy.deepcopy(self.expr)
    def eval(self, ctx):
        for k, v in self.args.items():
            ctx.store(k, v.eval(ctx))
        return self.expr.eval(ctx)
    def __str__(self):
        return 'f(%s) { %s }' % (', '.join(self.args), self.expr)

@operator('!min', '!max', '!spread', 'trigger')
class RandomWalk(Node):
    def setup(self):
        self.pos = (self.max + self.min) // 2
    def eval(self, ctx):
        if self.trigger.eval(ctx):
            self.pos += random.uniform(-self.spread, self.spread)
            self.pos = max(self.min, min(self.max, self.pos))
        return self.pos

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
                    if msg.note in notes:
                        del notes[msg.note]
                elif msg.type == 'control_change':
                    self.shim.set_control(msg.control, msg.value)
                self.shim.set_notes(notes)

@operator('!device', '!value')
class MIDIShim(Node):
    def setup(self):
        self.eq = Const(0)
        self.changed = 0
        self.controls = {}
        self.thread = MIDIThread(self)
        self.thread.daemon = True
        self.thread.start()
    def eval(self, ctx):
        if self.changed:
            self.changed = 0
            print(self.controls)
            for k, v in self.controls.items():
                ctx.store('CC%i' % k, v)
        return self.eq.eval(ctx)
    def set_notes(self, notes):
        self.eq = sum((FunctionCall(self.value, {'note': Int(k), 'velocity': Int(v)})
            for k, v in notes.items()), Const(0))
    def set_control(self, control, value):
        self.changed = 1
        self.controls[control] = value
