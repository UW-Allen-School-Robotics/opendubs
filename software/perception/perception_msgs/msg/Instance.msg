# One segmented object.
string label
float32 score
int32[4] box               # x0, y0, x1, y1 in rgb pixels
sensor_msgs/Image mask     # mono8, same size as rgb, 255 = object
