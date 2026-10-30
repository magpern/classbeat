"""Saving a class to the user's MOWL account (ADR 0007 sections 5, 6, 8 and 9).

- ``classbeat.saving.kind``: the ``save_workout`` job kind (input, ``admit``, the stages
  ``prepare``, ``save``, ``verify``), registered in ``classbeat.jobs.KINDS`` on import;
- ``classbeat.saving.approval``: the approval checks, at creation and again in the worker;
- ``classbeat.saving.operation``: the ``mowl_save_workout`` external operation (key, hash,
  context, bound, ``saved_workouts`` on success, the listing reconciler), registered in
  ``classbeat.operations.OPERATION_KINDS`` on import;
- ``classbeat.saving.update``: the ``update_workout`` job kind (SV2: input, ``admit``, the stages
  ``prepare``, ``read``, ``save``, ``verify``), registered in ``classbeat.jobs.KINDS`` on import;
- ``classbeat.saving.update_operation``: the ``mowl_update_workout`` external operation (key,
  hash with the program and playlist, context with ``before_hash`` and the baseline, the saved
  row moved on success, the target-plus-listing reconciler);
- ``classbeat.saving.reads``: the reconcilers' read of the saved revision.

Imported by the worker (which runs the kinds and configures their ``Runtime``) and by the web
app (the jobs API validates and admits a Save or an Update; Check again runs the reconcilers). A
job kind's own package (job kinds live outside ``classbeat.jobs``): it writes only through the
job's fence and the protocol, and reaches MOWL only through ``classbeat.connections``.
"""

from classbeat.saving.kind import KIND, SAVE_WORKOUT, Runtime, SaveWorkoutInput, configure
from classbeat.saving.operation import SAVE, configure_listing_lag
from classbeat.saving.update import KIND as UPDATE_KIND
from classbeat.saving.update import UPDATE_WORKOUT, UpdateWorkoutInput
from classbeat.saving.update_operation import UPDATE

__all__ = [
    "KIND",
    "SAVE",
    "SAVE_WORKOUT",
    "UPDATE",
    "UPDATE_KIND",
    "UPDATE_WORKOUT",
    "Runtime",
    "SaveWorkoutInput",
    "UpdateWorkoutInput",
    "configure",
    "configure_listing_lag",
]
