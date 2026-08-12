from sonolus.script.ui import UiMetric


def test_ui_metric_matches_the_sonolus_metric_union():
    # The EngineConfigurationMetric union from @sonolus/core 7.15.3 (Sonolus 1.1.3), in declaration order.
    assert [metric.value for metric in UiMetric] == [
        "arcade",
        "arcadePercentage",
        "accuracy",
        "accuracyPercentage",
        "life",
        "time",
        "perfect",
        "perfectPercentage",
        "greatGoodMiss",
        "greatGoodMissPercentage",
        "miss",
        "missPercentage",
        "errorHeatmap",
    ]
