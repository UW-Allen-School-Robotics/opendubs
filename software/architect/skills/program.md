<!-- ssa-meta
always_loaded: true
description: What every OpenDubs program must look like: a spoken summary line, primitives only, no catching refusals.
-->
# Writing a program for OpenDubs

The supervisor approves a program by voice after hearing its summary, and the same program then
runs again for every trick-or-treater. A program is checked before it is offered, and one that
breaks these rules is sent back to you.

## The summary line
The first line of every program is a comment the robot reads aloud for approval:

```python
# Summary: pick a candy from the bowl and hand it to the person in front of me.
```

One plain sentence, under 20 words, saying what the robot will do. No code, no numbers.

## What a program may use
- The primitives in the API, Python's builtins, and functions the program defines itself.
- `import math` and `import time` only. Nothing else may be imported.
- Write any helper you need inside the program. Only what is in the program's text runs.

## Never catch a refusal
- No bare `except:`, and no `except Exception`, `except RuntimeError` or `except MotionRefused`.
  A refusal or a failed check must end the run, so the supervisor sees it.
- To stop on a condition of your own, test it and raise `RuntimeError("why")`, with a reason the
  robot can say aloud.

## One run, one person
The program runs once per "trick or treat": start from `reset_robot()`, serve one person, and end
stowed. Do not loop waiting for the next person; the trigger does that.
