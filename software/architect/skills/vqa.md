<!-- ssa-meta
always_loaded: true
description: Asking the cameras questions on OpenDubs: which camera, the reply shape, and what never to ask about people.
-->
# Visual question answering

`get_vqa_response(prompt, camera="head")` returns `{"data": {"answer": "<string>"}}`.
Always read `result["data"]["answer"]`.

## Cameras
- `"head"` (default): the Orbbec on the pan/tilt head. Sees the bowl, the arms and the person.
  Point it first with `set_head_pose(pan_deg, tilt_deg)` if the target may be out of view.
- `"wrist_left"`, `"wrist_right"`: ZED Mini cameras on the wrists, **only if fitted**. A call to a
  camera that is not fitted raises an error. Prefer them for checking a grasp up close.

## When to use it
- A yes/no check of an outcome the joints cannot show (see verification.md).
- A short list of object names: "List only the names of the wrapped candies you can see, comma-separated."

## When not to
- To find where something is: use `detect_objects` or `detect_markers`; VQA returns no poses.
- Arm or gripper state: use `get_current_ee_pose`, `get_current_gripper_width`.

## People
The robot works in front of children. Ask about **objects and hands only**: "Is a hand held out
in front of the robot?", "Is the candy in the person's hand?". Never ask about a person's
appearance, age, costume, face or identity, and never repeat such a description aloud.
