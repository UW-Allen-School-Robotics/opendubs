# Third-party notices for the tool registry

## Surge hand (Alt-Bionics)

`surge_right.yaml` and `surge_left.yaml` describe the Alt-Bionics Surge v1.3 hand. Its twin model is
the vendor's URDF and meshes, which this repository does **not** contain: `sim/hand.py` loads them from
the folder named by `$OPENDUBS_SURGE_DIR`. The joint names, limits and the two model fixes recorded in
`surge_right.yaml` come from that URDF, which carries this licence:

> Copyright 2025 Alt-Bionics Inc
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
> associated documentation files (the "Software"), to deal in the Software without restriction,
> including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense,
> and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so,
> subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or substantial
> portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
> LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
> NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
> WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
> SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

The crawl gaits' parameters and speeds in `surge_right.yaml` are simulation results: the forward gaits,
heading loop and leash figure from the project's Surge Hand investigation, the back-up gait and pivots
from the Choreography lane, both with the same gait model.
