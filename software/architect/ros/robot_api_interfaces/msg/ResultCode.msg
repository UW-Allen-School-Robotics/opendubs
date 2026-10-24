# How a /robot/ service call ended. architect-franka's codes, plus OpenDubs' REFUSED.
uint8 SUCCESS               = 0
uint8 FAILURE               = 1
uint8 INVALID_INPUT         = 2
uint8 TIMEOUT               = 3
uint8 SERVICE_NOT_RUNNING   = 4
uint8 REFUSED               = 5   # a safety gate said no: the program stops (MotionRefused)

uint8 result_code
string message
