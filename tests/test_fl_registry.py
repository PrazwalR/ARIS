import pytest

from aris.fl.registry import ModelRegistry, should_rollback


@pytest.fixture
def registry(tmp_path):
    return ModelRegistry(tmp_path)


class TestRegister:
    def test_first_registration_becomes_active_automatically(self, registry):
        registry.register("v1", "synthetic", "ckpt_v1.npz", {"auc": 0.6})
        assert registry.active_version == "v1"

    def test_activate_false_does_not_change_active_version(self, registry):
        registry.register("v1", "synthetic", "ckpt_v1.npz", {"auc": 0.6})
        registry.register("v2", "synthetic", "ckpt_v2.npz", {"auc": 0.55}, activate=False)
        assert registry.active_version == "v1"

    def test_activate_true_switches_active_version(self, registry):
        registry.register("v1", "synthetic", "ckpt_v1.npz", {"auc": 0.6})
        registry.register("v2", "synthetic", "ckpt_v2.npz", {"auc": 0.65}, activate=True)
        assert registry.active_version == "v2"

    def test_duplicate_version_rejected(self, registry):
        registry.register("v1", "synthetic", "ckpt_v1.npz", {"auc": 0.6})
        with pytest.raises(ValueError):
            registry.register("v1", "synthetic", "ckpt_v1_again.npz", {"auc": 0.61})

    def test_history_is_chronological(self, registry):
        registry.register("v1", "synthetic", "a.npz", {"auc": 0.6})
        registry.register("v2", "synthetic", "b.npz", {"auc": 0.65})
        registry.register("v3", "synthetic", "c.npz", {"auc": 0.7})
        assert [r.model_version for r in registry.history()] == ["v1", "v2", "v3"]


class TestRollback:
    def test_rollback_switches_active_pointer(self, registry):
        registry.register("v1", "synthetic", "a.npz", {"auc": 0.6})
        registry.register("v2", "synthetic", "b.npz", {"auc": 0.5})  # regression
        registry.rollback_to("v1")
        assert registry.active_version == "v1"

    def test_rollback_keeps_rolled_back_from_version_in_history(self, registry):
        registry.register("v1", "synthetic", "a.npz", {"auc": 0.6})
        registry.register("v2", "synthetic", "b.npz", {"auc": 0.5})
        registry.rollback_to("v1")
        assert {r.model_version for r in registry.history()} == {"v1", "v2"}

    def test_rollback_to_unregistered_version_raises(self, registry):
        registry.register("v1", "synthetic", "a.npz", {"auc": 0.6})
        with pytest.raises(ValueError):
            registry.rollback_to("v99")


class TestPersistence:
    def test_survives_reload_from_disk(self, tmp_path):
        reg = ModelRegistry(tmp_path)
        reg.register("v1", "synthetic", "a.npz", {"auc": 0.6})
        reg.register("v2", "synthetic", "b.npz", {"auc": 0.65})
        reg.rollback_to("v1")

        reloaded = ModelRegistry(tmp_path)
        assert reloaded.active_version == "v1"
        assert len(reloaded.history()) == 2
        assert reloaded.get("v2").metrics["auc"] == pytest.approx(0.65)

    def test_empty_registry_has_no_active_record(self, registry):
        assert registry.active_version is None
        assert registry.active_record() is None


class TestShouldRollback:
    def test_flags_a_real_regression(self):
        assert should_rollback({"auc": 0.55}, {"auc": 0.65}) is True

    def test_does_not_flag_an_improvement(self):
        assert should_rollback({"auc": 0.70}, {"auc": 0.65}) is False

    def test_small_regression_within_tolerance_is_not_flagged(self):
        assert should_rollback({"auc": 0.649}, {"auc": 0.65}, max_regression=0.02) is False

    def test_missing_metric_on_either_side_is_not_a_rollback(self):
        assert should_rollback({}, {"auc": 0.65}) is False
        assert should_rollback({"auc": 0.5}, {}) is False

    def test_custom_metric_and_tolerance(self):
        assert (
            should_rollback(
                {"pr_auc": 0.30}, {"pr_auc": 0.40}, metric="pr_auc", max_regression=0.05
            )
            is True
        )
