from enum import StrEnum


class Op(StrEnum):
    def __new__(cls, name: str, side_effects: bool, pure: bool, control_flow: bool):
        obj = str.__new__(cls, name)
        obj._value_ = name
        obj.side_effects = side_effects
        obj.pure = pure
        obj.control_flow = control_flow
        return obj

    Abs = ("Abs", False, True, False)
    Add = ("Add", False, True, False)
    And = ("And", False, True, True)
    Arccos = ("Arccos", False, True, False)
    Arcsin = ("Arcsin", False, True, False)
    Arctan = ("Arctan", False, True, False)
    Arctan2 = ("Arctan2", False, True, False)
    BeatToBPM = ("BeatToBPM", False, True, False)
    BeatToStartingBeat = ("BeatToStartingBeat", False, True, False)
    BeatToStartingTime = ("BeatToStartingTime", False, True, False)
    BeatToTime = ("BeatToTime", False, True, False)
    Block = ("Block", False, True, True)
    Break = ("Break", True, False, True)
    Ceil = ("Ceil", False, True, False)
    Clamp = ("Clamp", False, True, False)
    Copy = ("Copy", True, False, False)
    Cos = ("Cos", False, True, False)
    Cosh = ("Cosh", False, True, False)
    DebugLog = ("DebugLog", True, False, False)
    DebugPause = ("DebugPause", True, False, False)
    DecrementPost = ("DecrementPost", True, False, False)
    DecrementPostPointed = ("DecrementPostPointed", True, False, False)
    DecrementPostShifted = ("DecrementPostShifted", True, False, False)
    DecrementPre = ("DecrementPre", True, False, False)
    DecrementPrePointed = ("DecrementPrePointed", True, False, False)
    DecrementPreShifted = ("DecrementPreShifted", True, False, False)
    Degree = ("Degree", False, True, False)
    DestroyParticleEffect = ("DestroyParticleEffect", True, False, False)
    Divide = ("Divide", False, True, False)
    DoWhile = ("DoWhile", False, True, True)
    Draw = ("Draw", True, False, False)
    DrawCurvedB = ("DrawCurvedB", True, False, False)
    DrawCurvedBT = ("DrawCurvedBT", True, False, False)
    DrawCurvedL = ("DrawCurvedL", True, False, False)
    DrawCurvedLR = ("DrawCurvedLR", True, False, False)
    DrawCurvedR = ("DrawCurvedR", True, False, False)
    DrawCurvedT = ("DrawCurvedT", True, False, False)
    EaseInBack = ("EaseInBack", False, True, False)
    EaseInCirc = ("EaseInCirc", False, True, False)
    EaseInCubic = ("EaseInCubic", False, True, False)
    EaseInElastic = ("EaseInElastic", False, True, False)
    EaseInExpo = ("EaseInExpo", False, True, False)
    EaseInOutBack = ("EaseInOutBack", False, True, False)
    EaseInOutCirc = ("EaseInOutCirc", False, True, False)
    EaseInOutCubic = ("EaseInOutCubic", False, True, False)
    EaseInOutElastic = ("EaseInOutElastic", False, True, False)
    EaseInOutExpo = ("EaseInOutExpo", False, True, False)
    EaseInOutQuad = ("EaseInOutQuad", False, True, False)
    EaseInOutQuart = ("EaseInOutQuart", False, True, False)
    EaseInOutQuint = ("EaseInOutQuint", False, True, False)
    EaseInOutSine = ("EaseInOutSine", False, True, False)
    EaseInQuad = ("EaseInQuad", False, True, False)
    EaseInQuart = ("EaseInQuart", False, True, False)
    EaseInQuint = ("EaseInQuint", False, True, False)
    EaseInSine = ("EaseInSine", False, True, False)
    EaseOutBack = ("EaseOutBack", False, True, False)
    EaseOutCirc = ("EaseOutCirc", False, True, False)
    EaseOutCubic = ("EaseOutCubic", False, True, False)
    EaseOutElastic = ("EaseOutElastic", False, True, False)
    EaseOutExpo = ("EaseOutExpo", False, True, False)
    EaseOutInBack = ("EaseOutInBack", False, True, False)
    EaseOutInCirc = ("EaseOutInCirc", False, True, False)
    EaseOutInCubic = ("EaseOutInCubic", False, True, False)
    EaseOutInElastic = ("EaseOutInElastic", False, True, False)
    EaseOutInExpo = ("EaseOutInExpo", False, True, False)
    EaseOutInQuad = ("EaseOutInQuad", False, True, False)
    EaseOutInQuart = ("EaseOutInQuart", False, True, False)
    EaseOutInQuint = ("EaseOutInQuint", False, True, False)
    EaseOutInSine = ("EaseOutInSine", False, True, False)
    EaseOutQuad = ("EaseOutQuad", False, True, False)
    EaseOutQuart = ("EaseOutQuart", False, True, False)
    EaseOutQuint = ("EaseOutQuint", False, True, False)
    EaseOutSine = ("EaseOutSine", False, True, False)
    Equal = ("Equal", False, True, False)
    Execute = ("Execute", False, True, False)
    Execute0 = ("Execute0", False, True, False)
    ExportValue = ("ExportValue", True, False, False)
    Floor = ("Floor", False, True, False)
    Frac = ("Frac", False, True, False)
    Get = ("Get", False, False, False)
    GetPointed = ("GetPointed", False, False, False)
    GetShifted = ("GetShifted", False, False, False)
    Greater = ("Greater", False, True, False)
    GreaterOr = ("GreaterOr", False, True, False)
    HasEffectClip = ("HasEffectClip", False, True, False)
    HasParticleEffect = ("HasParticleEffect", False, True, False)
    HasSkinSprite = ("HasSkinSprite", False, True, False)
    If = ("If", False, True, True)
    IncrementPost = ("IncrementPost", True, False, False)
    IncrementPostPointed = ("IncrementPostPointed", True, False, False)
    IncrementPostShifted = ("IncrementPostShifted", True, False, False)
    IncrementPre = ("IncrementPre", True, False, False)
    IncrementPrePointed = ("IncrementPrePointed", True, False, False)
    IncrementPreShifted = ("IncrementPreShifted", True, False, False)
    Judge = ("Judge", False, True, False)
    JudgeSimple = ("JudgeSimple", False, True, False)
    JumpLoop = ("JumpLoop", False, True, True)
    Lerp = ("Lerp", False, True, False)
    LerpClamped = ("LerpClamped", False, True, False)
    Less = ("Less", False, True, False)
    LessOr = ("LessOr", False, True, False)
    Log = ("Log", False, True, False)
    Max = ("Max", False, True, False)
    Min = ("Min", False, True, False)
    Mod = ("Mod", False, True, False)
    MoveParticleEffect = ("MoveParticleEffect", True, False, False)
    Multiply = ("Multiply", False, True, False)
    Negate = ("Negate", False, True, False)
    Not = ("Not", False, True, False)
    NotEqual = ("NotEqual", False, True, False)
    Or = ("Or", False, True, True)
    Paint = ("Paint", True, False, False)
    Play = ("Play", True, False, False)
    PlayLooped = ("PlayLooped", True, False, False)
    PlayLoopedScheduled = ("PlayLoopedScheduled", True, False, False)
    PlayScheduled = ("PlayScheduled", True, False, False)
    Power = ("Power", False, True, False)
    Print = ("Print", True, False, False)
    Radian = ("Radian", False, True, False)
    Random = ("Random", False, False, False)
    RandomInteger = ("RandomInteger", False, False, False)
    Rem = ("Rem", False, True, False)
    Remap = ("Remap", False, True, False)
    RemapClamped = ("RemapClamped", False, True, False)
    Round = ("Round", False, True, False)
    Set = ("Set", True, False, False)
    SetAdd = ("SetAdd", True, False, False)
    SetAddPointed = ("SetAddPointed", True, False, False)
    SetAddShifted = ("SetAddShifted", True, False, False)
    SetDivide = ("SetDivide", True, False, False)
    SetDividePointed = ("SetDividePointed", True, False, False)
    SetDivideShifted = ("SetDivideShifted", True, False, False)
    SetMod = ("SetMod", True, False, False)
    SetModPointed = ("SetModPointed", True, False, False)
    SetModShifted = ("SetModShifted", True, False, False)
    SetMultiply = ("SetMultiply", True, False, False)
    SetMultiplyPointed = ("SetMultiplyPointed", True, False, False)
    SetMultiplyShifted = ("SetMultiplyShifted", True, False, False)
    SetPointed = ("SetPointed", True, False, False)
    SetPower = ("SetPower", True, False, False)
    SetPowerPointed = ("SetPowerPointed", True, False, False)
    SetPowerShifted = ("SetPowerShifted", True, False, False)
    SetRem = ("SetRem", True, False, False)
    SetRemPointed = ("SetRemPointed", True, False, False)
    SetRemShifted = ("SetRemShifted", True, False, False)
    SetShifted = ("SetShifted", True, False, False)
    SetSubtract = ("SetSubtract", True, False, False)
    SetSubtractPointed = ("SetSubtractPointed", True, False, False)
    SetSubtractShifted = ("SetSubtractShifted", True, False, False)
    Sign = ("Sign", False, True, False)
    Sin = ("Sin", False, True, False)
    Sinh = ("Sinh", False, True, False)
    Spawn = ("Spawn", True, False, False)
    SpawnParticleEffect = ("SpawnParticleEffect", True, False, False)
    StackEnter = ("StackEnter", True, False, False)
    StackGet = ("StackGet", False, False, False)
    StackGetFrame = ("StackGetFrame", False, False, False)
    StackGetFramePointer = ("StackGetFramePointer", False, False, False)
    StackGetPointer = ("StackGetPointer", False, False, False)
    StackGrow = ("StackGrow", True, False, False)
    StackInit = ("StackInit", True, False, False)
    StackLeave = ("StackLeave", True, False, False)
    StackPop = ("StackPop", True, False, False)
    StackPush = ("StackPush", True, False, False)
    StackSet = ("StackSet", True, False, False)
    StackSetFrame = ("StackSetFrame", True, False, False)
    StackSetFramePointer = ("StackSetFramePointer", True, False, False)
    StackSetPointer = ("StackSetPointer", True, False, False)
    StopLooped = ("StopLooped", True, False, False)
    StopLoopedScheduled = ("StopLoopedScheduled", True, False, False)
    Subtract = ("Subtract", False, True, False)
    Switch = ("Switch", False, True, True)
    SwitchInteger = ("SwitchInteger", False, True, True)
    SwitchIntegerWithDefault = ("SwitchIntegerWithDefault", False, True, True)
    SwitchWithDefault = ("SwitchWithDefault", False, True, True)
    Tan = ("Tan", False, True, False)
    Tanh = ("Tanh", False, True, False)
    TimeToScaledTime = ("TimeToScaledTime", False, True, False)
    TimeToStartingScaledTime = ("TimeToStartingScaledTime", False, True, False)
    TimeToStartingTime = ("TimeToStartingTime", False, True, False)
    TimeToTimeScale = ("TimeToTimeScale", False, True, False)
    Trunc = ("Trunc", False, True, False)
    Unlerp = ("Unlerp", False, True, False)
    UnlerpClamped = ("UnlerpClamped", False, True, False)
    While = ("While", False, True, True)
