from sonolus.script.archetype import PlayArchetype, StandardArchetypeName, StandardImport, imported


class BpmChange(PlayArchetype):
    name = StandardArchetypeName.BPM_CHANGE

    beat: StandardImport.BEAT = imported()
    bpm: StandardImport.BPM = imported()


class TimescaleChange(PlayArchetype):
    name = StandardArchetypeName.TIMESCALE_CHANGE

    beat: StandardImport.BEAT = imported()
    timescale: StandardImport.TIMESCALE = imported()
