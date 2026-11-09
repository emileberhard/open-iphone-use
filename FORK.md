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
is added through the CLI, HTTP API or MCP. iOS applies the layout of whichever keyboard is active,
and third-party keyboards always get U.S. whatever the settings say (measured on an iPhone 14 Plus
with the Mumla keyboard, iOS 27.0.1), so agent devices set every language to U.S. The U.S. table
types å, ä, ö, ü, é, their capitals, €, £, § and ° through the layout's Option keys and
dead keys, all measured on that phone except Ü, which uses ü's dead key with Shift. `type_text`, `send_key` and `ensure_session`'s app
launch encode through that layout, including dead-key compositions such as `é` and `~`, and
reject text the layout cannot type before pressing any key. The Swedish table was measured on
the device for every key and modifier combination.

### 4. Stop idle screen streams

Decoding and re-encoding a phone's stream costs about half a CPU core even when nothing on the
screen changes. Each phone's stream now stops after five minutes without a snapshot, frame,
live view or action (`serve --idle-minutes`), and the next one restarts it and waits for a live
frame, about 1.2 s over USB. Frame IDs from before the pause are no longer valid.

### 5. Hand the USB connection to the phone that is asked for

The userspace network stack allows one USB tunnel per process, and every phone used to connect
when the server started, so the first registered phone held USB and the others failed with "No
frame received" until it was removed. Phones now connect on their first request, and a phone
asked for while another holds USB takes the connection over once the holder has gone three
seconds without a request, with no action or screenshot in progress and no live view open. The
requests of one ios-step call follow each other well inside three seconds, so the rule tells a
phone between an agent's calls from one in the middle of a call; nothing holds a lease across a
call, though. A switch waits for the rest of the holder's three seconds, then for its teardown
and the new connection, and the phone that gave up USB reconnects on its next request in about
1.2 s. While it waits, a phone's status names the holder and how long ago it was used, and it
retries every half second.

None of these changes has been offered upstream.

## Known gaps

**Frames are not turned upright.** On an iPad held in portrait or upside down, agents see the
interface rotated inside the landscape frame. Touches still land where the agent aims in that
image.

**One iPad model checked.** The landscape mapping and the right-and-bottom padding were checked
on an iPad Pro 11-inch (3rd generation) only.

**Two keyboard layouts.** Only US and Swedish have tables.

## Upstream audit obligation

On each upstream release, check whether upstream now handles iPad touches, encoder padding,
keyboard layouts or several USB phones itself, and drop any commit it makes redundant when
rebasing `ios-control` onto the new tag.
