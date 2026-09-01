from __future__ import annotations

from lifeos.kernel import LifeOSIntelligenceKernel


class FakeGBrain:
    def query(self, question: str, *, limit: int = 10):
        return {
            "results": [
                {
                    "text": "Project Atlas is in review.",
                    "path": "04-work/projects/atlas.md",
                    "confidence": "high",
                }
            ]
        }


class FakePgGraph:
    def health(self):
        return {"available": True, "derived": True, "canonical": False}


def test_kernel_packet_is_bounded_read_only_and_supports_not_modified(brain):
    kernel = LifeOSIntelligenceKernel(brain, gbrain=FakeGBrain(), pggraph=FakePgGraph())
    packet = kernel.turn_context(purpose="Prepare for Atlas", subjects=("Atlas",))
    assert packet.not_modified is False
    assert packet.current_facts[0]["claim"] == "Project Atlas is in review."
    assert packet.coverage["current_sources"] == ["gbrain"]
    assert packet.constraints[0]["rule"].startswith("canonical Markdown wins")
    unchanged = kernel.turn_context(
        purpose="Prepare for Atlas",
        subjects=("Atlas",),
        known_digest=packet.digest,
    )
    assert unchanged.not_modified is True
    assert unchanged.current_facts == ()
    assert unchanged.evidence == ()


def test_kernel_does_not_dump_unbounded_results(brain):
    class LargeGBrain:
        def query(self, question: str, *, limit: int = 10):
            return {"results": [{"text": "x" * 10000} for _ in range(50)]}

    kernel = LifeOSIntelligenceKernel(brain, gbrain=LargeGBrain(), pggraph=FakePgGraph())
    packet = kernel.turn_context(purpose="bounded")
    assert len(packet.evidence) <= 8
    assert sum(len(str(item)) for item in packet.evidence) < 9000
