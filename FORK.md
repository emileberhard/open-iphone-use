# ios-control fork of Open iPhone Use

This fork lets the agents behind ios-control, an agent skill that operates iPhones and iPads,
drive an iPad as reliably as upstream drives an iPhone. Upstream was built and checked on
iPhones. On an iPad every touch landed somewhere else, frames carried a black band, and a
phone set to a non-US hardware keyboard layout garbled typed punctuation.

**Base:** `e00c1ff3704a74e6f34b46e4b510eeb302716daf`, upstream tag `v0.1.1`.
**Default branch:** `ios-control`. `main` tracks upstream; upstream history is kept and its
commits are never modified.

## The delta

### 1. Turn touches from landscape frames onto the portrait touch panel

An iPad's display stream arrives in the panel's landscape frame, while the universal HID
touchscreen takes coordinates in its portrait frame. Both are fixed to the hardware: when the
interface rotates, the picture rotates inside the frame and neither coordinate system changes.
Touches from a landscape frame therefore turn onto the panel with one fixed mapping, and
portrait frames, as on iPhones, map unchanged. Checked with one tap in each of the four
interface orientations ([hardware record](docs/coredevice.md#validation-record)).

### 2. Crop frames to the display's pixel size

The HEVC encoder pads frames to its own size, 2400×1680 for a 2388×1668 iPad, with black bands
on the right and bottom. Touch fractions taken from the padded frame drifted toward those
edges. The stream connection reads the display's size from one CoreDevice screenshot when it
connects and crops every frame to it.

### 3. Type with the phone's hardware keyboard layout

The virtual keyboard sends HID usages, which iOS reads through the hardware keyboard layout
chosen on the device. Each phone now records its layout (`us` by default, or `sv`), set when it
is added through the CLI, HTTP API or MCP. `type_text`, `send_key` and `ensure_session`'s app
launch encode through that layout, including dead-key compositions such as `é` and `~`, and
reject text the layout cannot type before pressing any key. The Swedish table was measured on
the device for every key and modifier combination.

None of these changes has been offered upstream.

## Known gaps

**Frames are not turned upright.** On an iPad held in portrait or upside down, agents see the
interface rotated inside the landscape frame. Touches still land where the agent aims in that
image.

**One iPad model checked.** The landscape mapping and the right-and-bottom padding were checked
on an iPad Pro 11-inch (3rd generation) only.

**Two keyboard layouts.** Only US and Swedish have tables.

## Upstream audit obligation

On each upstream release, check whether upstream now handles iPad touches, encoder padding or
keyboard layouts itself, and drop any commit it makes redundant when rebasing `ios-control` onto
the new tag.
