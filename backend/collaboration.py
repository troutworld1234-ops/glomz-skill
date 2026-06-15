"""Collaboration module adapter.

Provides 'collab' namespace for app.py compatibility.
All functions are imported from collaboration_engine.
"""
from collaboration_engine import (
    get_rounds,
    list_patches,
    accept_patch,
    reject_patch,
    get_revision_history,
    extract_lessons_from_battle,
    get_lessons,
    get_context_bump,
    get_agent_specializations,
    set_agent_specializations,
    add_agent_specialization,
    start_round,
    end_round,
    get_current_round,
    check_auto_join,
    record_agent_join,
    create_patch,
    record_lesson,
    mark_lesson_applied,
    auto_invite_after_create,
    get_matching_agents,
    on_battle_close,
    init_collaboration_tables,
    get_revision_history,
)

# Create a namespace object for collab.method() style access
class _CollabNamespace:
    def __init__(self):
        self.get_rounds = get_rounds
        self.list_patches = list_patches
        self.accept_patch = accept_patch
        self.reject_patch = reject_patch
        self.get_revision_history = get_revision_history
        self.extract_lessons_from_battle = extract_lessons_from_battle
        self.get_lessons = get_lessons
        self.get_context_bump = get_context_bump
        self.get_agent_specializations = get_agent_specializations
        self.set_agent_specializations = set_agent_specializations
        self.add_agent_specialization = add_agent_specialization
        self.start_round = start_round
        self.end_round = end_round
        self.get_current_round = get_current_round
        self.check_auto_join = check_auto_join
        self.record_agent_join = record_agent_join
        self.create_patch = create_patch
        self.record_lesson = record_lesson
        self.mark_lesson_applied = mark_lesson_applied
        self.auto_invite_after_create = auto_invite_after_create
        self.get_matching_agents = get_matching_agents
        self.on_battle_close = on_battle_close
        self.init_collaboration_tables = init_collaboration_tables

collab = _CollabNamespace()
