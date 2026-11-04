# The outcome of a RobotCommand or RobotQuery call.
uint8 SUCCESS               = 0
uint8 FAILURE               = 1   # the request was valid and the service ran, but it could not do it
uint8 INVALID_INPUT         = 2   # bad JSON, a missing field, or a request it cannot serve as asked
uint8 TIMEOUT               = 3
uint8 SERVICE_NOT_RUNNING   = 4   # something it depends on is down or unreachable
uint8 NOT_FOUND             = 5   # nothing matched (e.g. no object with that label in view)

uint8 result_code
string message
