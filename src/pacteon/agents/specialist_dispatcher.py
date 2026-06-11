"""
SpecialistDispatcher — Stage 2.5 orchestrator.

Pure Python. Routes each MachineProcess to its domain specialist, merges corrections,
recalculates process times with corrected parameters, then runs the EngDocSpecialist
for standards compliance validation across all processes.
"""
import anthropic

from ..models.drawing import ExtractedDrawing
from ..models.machine import MachineProcess
from ..tools.process_calculator import calc_process_time, calc_setup_time, calc_process_cost

from .cutting_specialist import CuttingSpecialistAgent
from .forming_specialist import FormingSpecialistAgent
from .machining_specialist import MachiningSpecialistAgent
from .welding_specialist import WeldingSpecialistAgent
from .finishing_specialist import FinishingSpecialistAgent
from .eng_doc_specialist import EngDocSpecialistAgent

DOMAIN_MAP: dict[str, set[str]] = {
    "cutting":   {"LASER_CUT", "TUBE_LASER", "WATERJET"},
    "forming":   {"PRESS_BRAKE"},
    "machining": {"CNC_MILL", "LATHE", "DRILL", "TAP"},
    "welding":   {"WELD_MIG", "WELD_TIG"},
    "finishing": {"PAINT", "POWDER_COAT", "ANODIZE", "BLAST", "ZINC_PLATE", "CHROMATE", "LASER_MARK"},
}

_DOMAIN_AGENT_CLASSES = {
    "cutting":   CuttingSpecialistAgent,
    "forming":   FormingSpecialistAgent,
    "machining": MachiningSpecialistAgent,
    "welding":   WeldingSpecialistAgent,
    "finishing": FinishingSpecialistAgent,
}


def _process_domain(process_id: str) -> str | None:
    for domain, ids in DOMAIN_MAP.items():
        if process_id in ids:
            return domain
    return None


def _recalculate(proc: MachineProcess, material_key: str, thickness_in: float | None, blank_area: float) -> MachineProcess:
    """Re-run deterministic time/cost formulas using the specialist's corrected parameters."""
    pid = proc.process_id or proc.machine_id
    run_time_hr = calc_process_time(
        process_id=pid,
        quantity=proc.quantity,
        material_key=material_key or "DEFAULT",
        thickness_in=thickness_in,
        blank_area_sq_in=blank_area,
        cut_length_in=proc.cut_length_in,
        pierce_count=proc.pierce_count,
    )
    setup_time_hr = calc_setup_time(pid, proc.quantity, proc.pierce_count)
    setup_cost, run_cost = calc_process_cost(pid, run_time_hr, setup_time_hr)
    labor_cost = setup_cost + run_cost

    return proc.model_copy(update={
        "estimated_time_hr": run_time_hr,
        "setup_time_hr": setup_time_hr,
        "labor_cost": labor_cost,
    })


class SpecialistDispatcher:
    """
    Coordinates all Stage 2.5 specialists.
    Call `review(drawing, processes)` to get a specialist-reviewed, recalculated list.
    """

    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    def review(self, drawing: ExtractedDrawing, processes: list[MachineProcess]) -> list[MachineProcess]:
        """
        Fan out domain-specific processes to their specialist agents, merge corrections,
        recalculate times, then run the standards gate (EngDocSpecialist).

        Falls back gracefully per-domain: if a specialist call fails, that domain's
        processes are returned unchanged.
        """
        if not processes:
            return processes

        # Group processes by domain, preserving original indices for merge
        domain_groups: dict[str, list[tuple[int, MachineProcess]]] = {}
        for i, proc in enumerate(processes):
            pid = proc.process_id or proc.machine_id
            domain = _process_domain(pid)
            if domain:
                domain_groups.setdefault(domain, []).append((i, proc))

        result = list(processes)  # mutable copy

        # Run each domain specialist
        for domain, indexed_procs in domain_groups.items():
            agent_class = _DOMAIN_AGENT_CLASSES.get(domain)
            if not agent_class:
                continue
            indices = [i for i, _ in indexed_procs]
            domain_procs = [p for _, p in indexed_procs]
            try:
                reviewed = agent_class(self.client).review(drawing, domain_procs)
                # Recalculate times with corrected parameters, then put back in position
                blank_area = drawing.length_in * drawing.width_in
                for orig_idx, rev_proc in zip(indices, reviewed):
                    recalculated = _recalculate(rev_proc, drawing.material_key or "DEFAULT", drawing.thickness_in, blank_area)
                    result[orig_idx] = recalculated
            except Exception:
                pass  # domain specialist failure is non-fatal; original assignments preserved

        # Run the standards gate across all (now-specialist-reviewed) processes
        try:
            eng_doc = EngDocSpecialistAgent(self.client)
            result = eng_doc.review(drawing, result)
        except Exception:
            pass  # standards gate failure is non-fatal

        return result
