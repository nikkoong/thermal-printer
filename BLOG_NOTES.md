# Blog notes: from a text prompt to a printed receipt

> Raw reference notes for a blog post about the MXW01 thermal printer, Bluetooth Low Energy
> (BLE), the vendor protocol, the local bridge, and the OpenCode plugin.
>
> **This is not a blog draft.** It is deliberately written bottom-up, assuming the reader knows
> nothing about Bluetooth Low Energy (BLE), the Generic Attribute Profile (GATT), binary protocols,
> or how data gets from software to hardware. Every acronym is expanded at first use and again in
> Appendix A. Pull explanations, analogies, tables, and code snippets from here when writing the
> real post.

## How to use these notes

The document is organized as a ladder. Each part assumes only the parts before it:

1. **Part 1 — Data and hardware:** what a bit is, how a computer sends one, buses, serial links,
   packets, checksums, and protocol layers.
2. **Part 2 — Wireless and BLE:** radio, advertising, connections, GATT, notifications, the maximum
   transmission unit (MTU), and the software stack.
3. **Part 3 — The MXW01 protocol:** the actual GATT layout, byte frames, cyclic redundancy checks
   (CRCs), commands, status, print sequence, and raster format.
4. **Part 4 — Rendering:** how text becomes a 384-pixel-wide, one-bit-per-pixel image, including
   fonts, baselines, receipt columns, dithering, and quick-response (QR) codes.
5. **Part 5 — The software:** Python, asyncio, Bleak, FastAPI, the transport abstraction, config,
   history, tests, and why a daemon owns the printer.
6. **Part 6 — The user experience:** the command-line interface (CLI), the web user interface (UI),
   and the OpenCode plugin, including `thermal serve` versus `thermal serve --open`.
7. **Part 7 — The bug log:** every significant failure, with symptom, cause, fix, and lesson.
8. **Part 8 — Reuse versus new work:** the GitHub projects that supplied protocol knowledge and
   what was built here.
9. **Part 9 — Printer tech in general:** what this project says about cheap peripherals, drivers,
   local-first software, and repairability.
10. **Part 10 — Ideas and future work:** agent-native printing, continuous integration (CI) hooks,
    the Model Context Protocol (MCP), labels, and more.

At the end there is a **glossary of every acronym**, a byte-level cheat sheet, a layer map, demo
numbers, and a suggested blog outline.

## The whole stack in one picture

Read this from the bottom up. Each layer adds meaning to the layer below it.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ 10. User experience                                                          │
│     CLI command, web form, OpenCode tool call, curl request                  │
├──────────────────────────────────────────────────────────────────────────────┤
│ 9. Application service                                                       │
│     FastAPI Hypertext Transfer Protocol (HTTP) server, JavaScript Object     │
│     Notation (JSON), endpoints, static web UI, history                       │
├──────────────────────────────────────────────────────────────────────────────┤
│ 8. Printer manager                                                           │
│     connect/reconnect, locking, protocol auto-detection, retries, copies     │
├──────────────────────────────────────────────────────────────────────────────┤
│ 7. Transport                                                                 │
│     Bleak → macOS CoreBluetooth / Linux BlueZ → BLE radio                    │
├──────────────────────────────────────────────────────────────────────────────┤
│ 6. Device protocol                                                           │
│     MXW01 frames, commands, status, print sequence, acknowledgements         │
├──────────────────────────────────────────────────────────────────────────────┤
│ 5. Raster format                                                             │
│     384 pixels per row, 1 bit per pixel, least-significant-bit-first         │
│     (LSB-first), 1 = black                                                   │
├──────────────────────────────────────────────────────────────────────────────┤
│ 4. Rendering                                                                 │
│     text → fonts → layout → grayscale → threshold/dither → 1-bit bitmap      │
├──────────────────────────────────────────────────────────────────────────────┤
│ 3. Link layer                                                                │
│     BLE advertising, connection events, packets, Attribute Protocol          │
│     (ATT)/GATT                                                               │
├──────────────────────────────────────────────────────────────────────────────┤
│ 2. Radio                                                                     │
│     2.4 GHz electromagnetic waves, modulation, antenna, received signal      │
│     strength indicator (RSSI)                                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ 1. Physics                                                                   │
│     voltages, currents, transistors, wires, timing                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

The important mental model: **the printer is a small computer with a radio, firmware, a buffer, and
a print head.** It is not a dumb motor that prints whatever bytes it receives. Every layer has
rules, state, and failure modes.

## The seven questions every data transfer must answer

When you send anything to any device, these questions must be answered somewhere, even if the
answer is hidden inside a library:

1. **Who is the receiver?** An address, a name, a universally unique identifier (UUID), a file
   descriptor, a pin.
2. **What is being sent?** Bits, bytes, text, an image, a command.
3. **Where does one message start and end?** Framing: headers, lengths, delimiters, terminators.
4. **How fast may I send?** Baud rate, connection interval, MTU, buffer size, flow control.
5. **In what order does it arrive?** Ordered stream, packets, sequence numbers, retries.
6. **Did it arrive correctly?** Checksums, CRCs, acknowledgements, timeouts.
7. **What does it mean?** The application protocol: command codes, payload layout, state machine.

A useful analogy is sending a letter:

- The **paper and ink** are the physical layer.
- The **letters and words** are the encoding.
- The **envelope and address** are framing and addressing.
- The **postal rules** are the protocol.
- The **return receipt** is an acknowledgement.
- The **language inside the letter** is the application protocol.

Each layer only reads its own envelope. The postal service does not need to understand the letter;
it only needs the address and the rules.

## The story in one paragraph

I had a small MXW01 thermal printer that only worked through the “Fun Print” phone app. I wanted my
coding agent to print physical notes. The printer advertises as `MXW01`, but its advertised service
(`af30`) is not the command service. After connecting and enumerating GATT, the real service is
`ae30` with `ae01` for commands, `ae02` for notifications, and `ae03` for raw image data. I
implemented the vendor framing (`22 21 <cmd> ...`), the 1-bit raster format, status and battery
queries, and a print flow. Then I built a Python package, CLI, FastAPI daemon, no-build web UI, and
an OpenCode plugin around it. Along the way: a wrong protocol assumption, a CRC variant mix-up,
inverted bit order, misaligned text baselines, and a printer that goes to sleep and drops off BLE.

---

# Part 1 — What “sending data to hardware” actually means

This part is the foundation. If you understand this, BLE and GATT become “just another set of
envelopes,” not magic.

## 1.1 Hardware is a collection of electrically controlled switches

A modern computer is built from **transistors**. A transistor is a tiny electrically controlled
switch: a small voltage on one terminal can control whether current flows between two others. Put
billions of them together and you can build logic gates, memory, and processors.

Digital hardware deliberately ignores most of the analog world. Instead of “somewhere between 0 and
5 volts,” it defines ranges:

- A **logic 0** might be 0–0.8 volts.
- A **logic 1** might be 2.0–3.3 volts.
- The gap in between is undefined and avoided.

This is why digital data is robust: a little noise does not change a 0 into a 1 unless it is large
enough to cross a threshold. It is like deciding that a light is “off” if it is dim and “on” if it
is bright; you do not care about the exact brightness.

**Analogy:** a byte is a row of eight light switches. Each switch is either off (0) or on (1).
Eight switches give 2⁸ = 256 possible patterns. A byte is simply a group of eight bits.

### Why two wires, and why “ground”

A voltage is a *difference* between two points. To read a signal, the receiver needs a shared
reference. That is what **ground** (GND) provides. A simple digital link therefore needs at least:

- a signal wire carrying the voltage that represents 0 or 1,
- a ground wire providing the reference,
- often a power wire if the device is not self-powered.

Some links use **differential signaling**: two wires carry opposite versions of the same signal. The
receiver looks at the *difference*. Noise that appears on both wires cancels out. Universal Serial
Bus (USB) and Ethernet use this idea. It is like two people shouting the same message and its
opposite; the listener subtracts one from the other, so background noise disappears.

### Timing: when is the wire valid?

A wire can change voltage at any time. The receiver needs to know *when* to look. Some links send a
separate **clock** signal; others agree on a **bit rate** in advance. This is the difference between
synchronous and asynchronous communication, covered in section 1.4.

**Analogy:** a conductor’s baton tells an orchestra when to play. Without timing, every musician
would guess, and the result would be noise.

### Why this matters for the printer

The printer’s radio, microcontroller, and print head are all digital hardware. The bytes you send
eventually become voltage patterns on a wire inside the printer. The print head then turns specific
bits into heat. The whole stack exists to make sure the right bits arrive at the right time.

## 1.2 Bits, bytes, hexadecimal, and endianness

### Bits and bytes

- **Bit:** one binary digit, 0 or 1. The smallest unit of data.
- **Byte:** eight bits. Most computers address memory in bytes.
- **Nibble:** four bits, or half a byte. One hexadecimal digit represents one nibble.

A byte can represent:

- an unsigned integer from 0 to 255,
- a signed integer from −128 to 127 (using two’s complement),
- an ASCII character,
- part of a larger number, image, or protocol field.

The meaning comes from context. The byte `0x41` is the number 65, the ASCII letter `A`, or one
pixel pattern, depending on who is reading it.

### Binary and hexadecimal

Binary is base 2: `0`, `1`, `10`, `11`, `100`, and so on. It is precise but long. Hexadecimal (hex)
is base 16: `0`–`9` and `A`–`F`. One hex digit is exactly four bits, so one byte is exactly two hex
digits.

Examples:

| Decimal | Binary | Hex |
| --- | --- | --- |
| 0 | `0000 0000` | `00` |
| 10 | `0000 1010` | `0A` |
| 65 | `0100 0001` | `41` |
| 93 | `0101 1101` | `5D` |
| 208 | `1101 0000` | `D0` |
| 255 | `1111 1111` | `FF` |

When you see `0x5D`, the `0x` prefix means “the following digits are hexadecimal.” So `0x5D` is 93
in decimal.

### ASCII and UTF-8

**ASCII** (American Standard Code for Information Interchange) maps 128 characters to numbers:
`A` = 65 = `0x41`, `a` = 97 = `0x61`, space = 32 = `0x20`, newline = 10 = `0x0A`.

**Unicode** is a much larger character set covering most of the world’s writing systems and symbols.
**UTF-8** is a variable-length encoding of Unicode: an ASCII character is one byte, while many other
characters take two to four bytes. This matters because a “character” is not always one byte.

**Analogy:** ASCII is a small dictionary with 128 entries. Unicode is a library with rooms for every
language. UTF-8 is a compact shelving system that stores common English words in one slot and rarer
characters in several slots.

### Endianness: which byte comes first?

A multi-byte number has to be stored in some order. Suppose the number is `0x1234`:

- **Big-endian:** most significant byte first: `12 34`.
- **Little-endian:** least significant byte first: `34 12`.

**Analogy:** saying “twelve thirty-four” versus “thirty-four twelve.” Both contain the same digits;
the convention tells you which part is the tens and which is the units.

The MXW01 uses **little-endian 16-bit** values for lengths and image heights. That is why a height of
208 (`0x00D0`) is sent as `D0 00`.

The protocol frame itself is:

```text
22 21 <command> 00 <length low> <length high> <payload> <crc8> FF
```

The two length bytes are little-endian. A payload length of 4 is `04 00`, not `00 04`.

### Why this matters for the printer

Endianness bugs produce plausible-looking but wrong values. A height of 208 sent as `00 D0` would be
interpreted as 53,248 rows, which would make the printer wait for far more image data than you sent.
The job would hang or fail, and the bytes would look “almost right.”

## 1.3 How the CPU (central processing unit) talks to a device: buses, controllers, drivers, and firmware

### The bus

A **bus** is a shared set of wires (or a shared logical channel) that connects a processor to
memory and peripherals. A simple bus has:

- **address lines:** which device or memory location is being addressed,
- **data lines:** the value being read or written,
- **control lines:** read/write, clock, interrupt, and so on.

**Analogy:** an office mailroom. The address tells you which pigeonhole to use; the data is the
envelope; the control signal says “put this in” or “take this out.”

Modern systems have many buses: memory buses, Peripheral Component Interconnect Express (PCIe),
USB, Serial Peripheral Interface (SPI), Inter-Integrated Circuit (I²C), and more. Each is a
different trade-off between speed, wire count, distance, and complexity.

### Controllers and peripherals

The processor rarely drives a wire directly. Instead, a **controller** chip handles the electrical
details. Examples:

- A **UART** (Universal Asynchronous Receiver/Transmitter) controller turns bytes into a serial
  voltage waveform and back.
- An **SPI** controller sends bits synchronously with a clock.
- An **I²C** controller sends addressed messages over two wires.
- A **USB** controller manages a complex, host-centric packet protocol.
- A **Bluetooth** controller manages radio channels, packets, encryption, and connections.

The processor writes to the controller’s registers, and the controller does the physical work.

**Analogy:** the processor is a manager writing instructions on a form. The controller is the
specialist who knows how to operate the machine. The manager does not need to know how to align the
radio’s antenna; it only needs to fill in the form correctly.

### Drivers

A **driver** is software that knows the rules of a controller or device. It translates a general
request (“write these bytes to the printer”) into the specific register writes, system calls, or
packets the hardware needs.

There are drivers at several levels:

- The **operating system** driver for the Bluetooth controller.
- The **Bluetooth stack** that implements BLE.
- A **user-space library** such as Bleak that gives Python a friendly application programming
  interface (API).
- The **device driver** in this project that knows the MXW01 command protocol.

**Analogy:** a translator at a front desk. You speak “I need to print this”; the translator speaks
“register 0x2A gets 0x01, then the radio packet goes on channel 37.”

### Firmware

**Firmware** is the software that runs inside the device. The printer’s firmware:

- advertises over BLE,
- exposes the GATT database,
- parses command frames,
- stores the image in a buffer,
- controls the print head,
- reports status, battery, temperature, and errors.

The firmware is fixed by the manufacturer. You cannot change it from the laptop (unless you flash a
custom build, which this project does not do). Your job is to speak its language correctly.

**Analogy:** the printer is a small restaurant with its own kitchen rules. You can order from the
menu, but you cannot rearrange the kitchen. If you send an item the kitchen does not recognize, it
may ignore it, reject it, or do something surprising.

### Interrupts, polling, and DMA

How does the processor know data arrived?

- **Polling:** the processor repeatedly asks, “Is there data yet?” Simple but wasteful.
- **Interrupt:** the controller sends a signal that says “stop what you are doing; data is here.”
  Efficient but requires careful handling.
- **Direct Memory Access (DMA):** the controller writes data directly into memory without the
  processor copying each byte. Useful for high-speed streams.

**Analogy:** polling is checking the front door every ten seconds. An interrupt is a doorbell. DMA
is a courier who puts the parcel directly on your desk.

### Buffers and flow control

A **buffer** is a temporary storage area. Devices have small buffers because memory is limited and
because the physical process (printing, radio transmission) is slower than the digital source.

If you send faster than the device can drain the buffer, data is lost, the device stalls, or the
protocol rejects it. **Flow control** is the set of rules that prevents this.

**Analogy:** a funnel. You can pour quickly for a moment, but if the narrow end cannot drain fast
enough, the funnel overflows. Good protocol design tells the sender when to slow down.

### Why this matters for the printer

The MXW01 has a radio buffer, a command parser, and an image buffer. It expects image data in
48-byte rows with a delay between rows. The BLE link could carry larger chunks, but the printer’s
firmware and print head set the real pace. “Send faster” is not automatically better.

## 1.4 Serial versus parallel

### Serial communication

**Serial** means one bit at a time over one lane (plus ground, and sometimes a clock). The bits are
sent in order: first bit, second bit, and so on. The receiver reassembles them into bytes.

Advantages:

- Fewer wires.
- Works over longer distances.
- No skew between parallel lanes.
- Modern high-speed links (USB, PCIe, Ethernet) are serial for these reasons.

Disadvantages:

- The receiver must know the bit rate and where a byte starts.
- Overhead for start/stop bits or framing.

### Parallel communication

**Parallel** means several bits at once over several lanes. An 8-bit parallel bus sends one byte per
clock tick across eight wires.

Advantages:

- Higher throughput at the same clock rate.
- Simple for short distances.

Disadvantages:

- More wires.
- **Skew:** bits on different wires arrive at slightly different times.
- Crosstalk and noise.
- Poor scaling over distance.

**Analogy:** serial is a single-lane drive-through. Parallel is eight lanes side by side. Eight lanes
look faster, but if the lanes are different lengths and cars arrive at different times, reassembling
the order becomes difficult. A single well-controlled lane is often better.

### Synchronous versus asynchronous

- **Synchronous:** a separate clock wire tells the receiver when each bit is valid. SPI and I²C are
  synchronous.
- **Asynchronous:** no shared clock; both sides agree on a bit rate, and the sender adds start/stop
  bits so the receiver can synchronize on each character. UART is asynchronous.

**Baud rate** is the number of signal changes per second. It is not always the same as bits per
second; in simple UARTs, one baud often equals one bit, but the framing bits add overhead.

**Analogy:** synchronous is a dance class with a conductor. Asynchronous is two people who agreed
to clap at the same tempo and use a count-in before each phrase.

### Where BLE fits

BLE is a **packet-based wireless serial link**. It is not a UART, but it has similar concerns:
timing, framing, addresses, error detection, and flow control. The radio replaces the wire; the
GATT layer replaces the “which register” conversation.

## 1.5 Packets, frames, checksums, and acknowledgements

### Why not send a raw stream?

If you connect two wires and send bytes, the receiver sees a stream. It does not know:

- where a message begins,
- where it ends,
- whether a byte was lost,
- whether the bytes belong to the same command,
- whether the sender is still talking.

A **protocol** adds structure. The basic unit is often called a **packet** or **frame**.

### The anatomy of a frame

A frame typically has:

```text
┌──────────┬──────────────┬─────────────┬──────────┬───────────┐
│ preamble │ header       │ payload     │ checksum │ terminator│
│ or magic │ (address,    │ (the actual │ (CRC,    │ or end    │
│          │  type, len)  │  data)      │ checksum)│ marker    │
└──────────┴──────────────┴─────────────┴──────────┴───────────┘
```

- **Preamble / magic:** a known byte pattern that helps the receiver find the start.
- **Header:** metadata such as command type, length, sequence number, or address.
- **Payload:** the data the command carries.
- **Checksum / CRC:** a mathematical summary used to detect corruption.
- **Terminator:** a known byte or pattern that marks the end.

**Analogy:** a shipping container. The container has a standard shape (magic), a manifest (header),
cargo (payload), a tamper seal (checksum), and a door that closes in a known way (terminator). The
port does not need to know what is inside the cargo; it only needs to move the container.

### Framing strategies

There are several common ways to find message boundaries:

1. **Length prefix:** “the payload is N bytes.” The receiver reads N bytes after the header.
   The MXW01 uses this.
2. **Delimiter:** a special byte marks the end. The receiver reads until it sees the delimiter.
   The MXW01 also has a trailing `FF`.
3. **Start/end patterns:** both ends are marked. Useful when payload can contain arbitrary bytes.
4. **Fixed length:** every message is the same size. Simple but inflexible.
5. **Escape sequences:** special bytes in the payload are escaped so they cannot be confused with
   delimiters.

Length prefixes are efficient but fragile if the length itself is corrupted. Delimiters are simple
but require escaping if the payload can contain the delimiter. The MXW01 combines a magic header, a
length, and a terminator, which gives the receiver several ways to resynchronize.

### Checksums, parity, and CRC

A **checksum** is a small value computed from the data. The receiver recomputes it and compares.

- **Parity:** one extra bit that makes the number of 1s even or odd. Detects single-bit errors.
- **Simple sum:** add all bytes modulo 256. Detects many errors but not all.
- **CRC (Cyclic Redundancy Check):** a polynomial division over the data. Much better at detecting
  burst errors than a simple sum.

**Analogy:** a tamper seal on a parcel. It does not tell you what is inside, but if the seal is
broken, you know something changed. A CRC is a mathematical seal: even a small change usually
produces a different value.

CRC is not encryption and not authentication. An attacker can recompute it. It detects accidental
corruption, not deliberate tampering.

### Acknowledgements and retries

Some protocols confirm delivery; some do not.

- **Acknowledged:** the receiver sends “got it.” The sender can retry if no acknowledgement arrives.
- **Unacknowledged:** the sender fires and forgets. Faster, but loss is invisible.
- **Sequence numbers:** let the receiver detect duplicates and reorder packets.
- **Timeouts:** if no acknowledgement arrives within a deadline, assume failure and retry or report.

**Analogy:** registered mail versus a postcard. Registered mail gives you a signature; a postcard
just disappears into the system. BLE has both modes: writes with response are registered mail;
writes without response are postcards. Notifications are the printer sending you a postcard.

### Why this matters for the printer

The MXW01 command frames have a magic header, a command byte, a length, a payload, a CRC, and a
terminator. The printer may or may not acknowledge each command. The driver has to tolerate missing
acknowledgements while still detecting real failures. That is a protocol-design and user-experience
problem, not just a coding problem.

## 1.6 Protocols and layers

### The layered model

Networking textbooks describe the **OSI model** (Open Systems Interconnection), a seven-layer
reference model:

1. **Physical:** voltages, wires, radio waves.
2. **Data link:** framing, addressing on the local link, error detection.
3. **Network:** routing between networks (for example, the Internet Protocol, IP).
4. **Transport:** end-to-end delivery, reliability, ports (for example, the Transmission Control
   Protocol, TCP, and the User Datagram Protocol, UDP).
5. **Session:** managing conversations.
6. **Presentation:** encoding, encryption, compression.
7. **Application:** the meaning of the data (HTTP, email, a printer command).

The real world often uses the simpler **TCP/IP model**: link, internet, transport, application.
BLE has its own stack: physical radio, link layer, ATT/GATT, and application profiles.

**Analogy:** nested envelopes. You write a letter (application). You put it in an envelope
addressed to a person (transport). The postal service puts that envelope in a sack (network) and
loads the sack onto a truck (link). The truck driver does not read the letter. Each layer adds its
own envelope and only reads the envelope addressed to it.

### Encapsulation

Each layer adds a header (and sometimes a trailer) to the data from the layer above:

```text
Application data
    ↓
Transport header + application data
    ↓
Network header + transport header + application data
    ↓
Link header + network header + transport header + application data + link trailer
    ↓
Bits on the wire / radio
```

The receiver reverses the process, stripping one envelope at a time.

### Why layering helps

- **Separation of concerns:** the radio does not need to know what a receipt is.
- **Replaceability:** you can swap Wi-Fi for Ethernet without rewriting the web application.
- **Testability:** you can test the renderer without a printer, and the protocol without a radio.
- **Debugging:** you can ask “which layer failed?” instead of “the printer is broken.”

### Bandwidth, latency, and throughput

These are different:

- **Bandwidth:** the maximum rate of a link, like the diameter of a pipe.
- **Latency:** how long one message takes to travel, like the length of the pipe.
- **Throughput:** how much actually gets through in practice, like the flow rate after leaks,
  traffic, and protocol overhead.

A wide pipe can still be slow if it is long or clogged. A BLE link with a large MTU can still be
slow if the connection interval is long or the device needs delays between writes.

**Analogy:** a highway with twelve lanes (bandwidth) can still have a traffic jam (low throughput),
and a short message can still take a long time to arrive if the delivery truck stops at every house
(latency).

### Reliability is end-to-end

A reliable link does not guarantee a reliable application. The radio may deliver every packet, but
the printer may drop a notification, reject a command, or run out of paper. The application must
still check state and report errors.

**Analogy:** a courier can confirm that the parcel reached the building, but that does not prove the
recipient opened it or liked what was inside.

## 1.7 The path of one byte to this printer

Here is the full journey, from a Python string to a black dot on paper:

```text
1. You type:        "BUILD GREEN"
2. Python string:   Unicode code points
3. Renderer:        glyphs drawn onto a 384-pixel-wide grayscale canvas
4. Threshold:       each pixel becomes black (0) or white (255)
5. Packing:         each row becomes 48 bytes; 1 = black, LSB-first
6. Protocol:        frame = 22 21 A9 00 04 00 D0 00 30 00 34 FF
7. Bleak:           write_gatt_char(AE01, frame, response=False)
8. OS Bluetooth:    CoreBluetooth / BlueZ queues an HCI packet
9. Controller:      radio modulates bits on a 2.4 GHz channel
10. Printer radio:  receives packets, reassembles ATT payloads
11. GATT server:    delivers the write to the AE01 characteristic
12. Firmware:       parses the frame, stores the print request
13. Image data:     48-byte rows arrive on AE03
14. Firmware:       buffers rows, controls the print head
15. Print head:     heating elements warm the thermal paper
16. Paper:          heat turns the coating black
17. You:            tear off the receipt
```

Every arrow has a buffer, a protocol, and a possible failure. The rest of this document zooms into
the interesting arrows.

## 1.8 Why this matters for the blog

The blog should not start with “I reverse-engineered a printer.” It should start with the problem:
a cheap device that only works through a phone app, and a desire to make it part of a local
workflow. Then the bottom-up explanation gives readers a reason to care about each layer:

- The physics explains why the printer is slow and why paper matters.
- The framing explains why one wrong byte produces garbage.
- The protocol explains why the phone app is the real driver.
- The rendering explains why the output looks like a receipt, not a screenshot.
- The architecture explains why a daemon is the right shape.
- The user experience (UX) explains why “just print” is a surprisingly deep request.

---

# Part 2 — Wireless, Bluetooth, and BLE from first principles

## 2.1 Radio in one page

### Electromagnetic waves

A radio transmitter drives an antenna with an alternating electrical signal. The antenna radiates
**electromagnetic waves**: oscillating electric and magnetic fields traveling through space.

Important properties:

- **Frequency:** how many cycles per second, measured in hertz (Hz). 1 Hz = one cycle per second.
  2.4 GHz = 2.4 billion cycles per second.
- **Wavelength:** the distance between cycles. At 2.4 GHz, it is about 12.5 cm (4.9 inches).
- **Amplitude:** the strength of the wave, related to power.
- **Modulation:** how the transmitter encodes bits by changing the wave.

**Analogy:** a radio wave is a ripple in a pond. Frequency is how rapidly the ripples arrive;
amplitude is how tall they are; modulation is a pattern of ripples that carries a message.

### The 2.4 GHz band

BLE, classic Bluetooth, and Wi-Fi all use the **2.4 GHz ISM band** (Industrial, Scientific, and
Medical). It is unlicensed, which is why cheap devices can use it without applying for a radio
license. The price is coexistence: many devices share the same air.

BLE divides the band into 40 channels. Three are **advertising channels** (often numbered 37, 38,
and 39); the rest are **data channels**. The radio hops between channels to reduce interference.

**Analogy:** a crowded party. Everyone is talking at once, so people move to different corners
(channels) and repeat themselves. Frequency hopping is the social version of changing corners
constantly so no single loud conversation blocks you forever.

### Power and RSSI

Radio power is measured in **dBm** (decibels relative to one milliwatt). The scale is logarithmic:

- 0 dBm = 1 milliwatt.
- −10 dBm = 0.1 milliwatt.
- −20 dBm = 0.01 milliwatt.
- Every −10 dBm is roughly one-tenth the power.

**RSSI** (Received Signal Strength Indicator) is the receiver’s measurement of how strong the
signal is, also in dBm. Values are usually negative:

- −40 to −60 dBm: strong, close.
- −60 to −80 dBm: moderate.
- −80 to −95 dBm: weak.
- Below −95 dBm: often unusable.

RSSI is noisy and depends on antennas, orientation, walls, and the human body. It is a hint, not a
distance measurement.

**Analogy:** how loudly someone’s voice reaches you across a room. A loud voice is probably close,
but a quiet person next to you can sound fainter than a loud person across the room.

### Why the printer uses radio instead of a cable

The product is a pocket printer. A cable would be simpler and faster, but the selling point is
untethered printing from a phone. BLE is cheap, low-power, and supported by every phone. The USB-C
port on this class of printer is usually charging-only; the data path is the radio.

## 2.2 Bluetooth Classic versus BLE

“Bluetooth” is a family of wireless standards. The two most relevant branches are:

### Bluetooth Classic (BR/EDR)

- **BR/EDR** = Basic Rate / Enhanced Data Rate.
- Designed for continuous links: audio headsets, speakers, file transfer, some game controllers.
- Higher throughput and more complex.
- Uses a different link layer from BLE.

### Bluetooth Low Energy (BLE)

- Introduced in Bluetooth 4.0.
- Designed for short, infrequent messages and low power.
- Uses advertising, connections, and the **Attribute Protocol (ATT)** / **Generic Attribute Profile
  (GATT)**.
- Common in sensors, fitness trackers, smart bulbs, and cheap printers.

Both share the 2.4 GHz band and the Bluetooth brand, but they are not interchangeable. A device may
support one or both. The DP-L1S family is **dual-mode**: it can appear as a classic device and as a
BLE device. On Linux, the operating system may try the classic side first and fail; the workaround
is to force BLE or use the `_BLE` identity. The MXW01 is BLE.

**Analogy:** Bluetooth Classic is a phone call that stays open. BLE is a series of short text
messages with a shared address book. Both use the same phone network, but the conversation patterns
are different.

### Roles in BLE

- **Central (client):** the device that scans and initiates connections. Your laptop.
- **Peripheral (server):** the device that advertises and waits. The printer.
- **Broadcaster:** sends advertising data without accepting connections.
- **Observer:** scans without connecting.

The central is not necessarily “more powerful”; it is simply the side that initiates. In GATT, the
peripheral is usually the server because it holds the data; the central is the client because it
reads and writes that data.

**Analogy:** the printer is a shop with a sign in the window (advertising). The laptop is a customer
who walks in (connection) and asks the clerk for items (GATT reads/writes).

## 2.3 Advertising: the shop sign

Before any connection, a BLE peripheral **advertises**. It periodically broadcasts a small packet on
the advertising channels. The packet can contain:

- flags indicating discoverability and whether classic Bluetooth is supported,
- the device’s local name (`MXW01`),
- a list of service UUIDs,
- transmit power,
- manufacturer-specific data,
- a service data field.

Because legacy advertising packets are small (31 bytes of payload), the device may put some
information in a **scan response**: a second packet sent only when a scanner actively asks for more.

### Active versus passive scanning

- **Passive scan:** just listen to advertising packets.
- **Active scan:** send a scan request; the peripheral may reply with a scan response containing
  additional data.

Bleak’s scanner uses the platform APIs and returns the name, address, RSSI, and service UUIDs it
received.

### Why the advertised service list is not the application programming interface (API)

Advertising data is a **hint**. It may be incomplete, stale, or refer to a different service than
the one used for commands. The MXW01 advertised `af30`, but the actual GATT table after connecting
contained `ae30`. The advertisement is like a shop sign that says “Open”; the real menu is inside.

**Blog angle:** this is a strong “trust but verify” story. The scan output looked authoritative, but
the device disagreed. The fix was to connect and enumerate, not to trust the advertisement.

### Addresses and identities

BLE devices have addresses, but the details vary by platform:

- **Public address:** a fixed Institute of Electrical and Electronics Engineers (IEEE)-style address.
- **Random address:** generated by the device, often for privacy; can rotate.
- **MAC address:** the 48-bit Media Access Control (MAC) hardware address familiar from Ethernet
  and classic Bluetooth.
- **UUID:** a 128-bit universally unique identifier. In GATT, UUIDs identify services and
  characteristics. On macOS, CoreBluetooth also assigns a UUID to each discovered peripheral; this
  is **not** a MAC address.

On macOS, `thermal scan` showed:

```text
* MXW01   6D717764-D3E3-2239-C52D-42759E25147F   -55 dBm
```

That long identifier is a CoreBluetooth UUID. It is specific to the scanning Mac and can change. The
stable identifier for this printer is its name, `MXW01`. On Linux with BlueZ, you usually see a MAC
address instead.

**Analogy:** a coat-check ticket versus a passport number. The ticket identifies your coat at this
particular cloakroom; the passport identifies the person. The ticket may change when you visit a
different cloakroom.

### Why the printer stops advertising

A BLE peripheral usually stops connectable advertising once a central connects. It may also stop
when it sleeps to save battery. If `thermal scan` does not show `MXW01`, the printer may be:

- asleep,
- already connected to the phone app,
- out of range,
- holding a stale connection,
- or simply not advertising at that moment.

This is physical state, not a software bug. The user interface (UI) has to explain it.

## 2.4 Connecting: the private conversation

### The connection event

When a central wants to connect, it sends a **connection request** containing parameters:

- **Connection interval:** how often the central and peripheral exchange packets. BLE intervals
  range from 7.5 milliseconds to 4 seconds, in 1.25 ms steps.
- **Peripheral latency (slave latency):** how many connection events the peripheral may skip if it
  has nothing to send. This saves battery.
- **Supervision timeout:** how long without a successful exchange before the connection is
  considered lost.

After the connection is established, the two sides hop together across data channels according to a
shared sequence. The central schedules the events; the peripheral wakes for each event (or skips
some, within the latency limit).

**Analogy:** a scheduled phone call. The connection interval is how often you call; latency is how
many calls the other person may skip; the supervision timeout is how long you wait before assuming
the other person is gone.

### Why BLE is low power

The peripheral’s radio is off most of the time. It wakes for a connection event, exchanges a few
packets, and sleeps again. The central coordinates the schedule. This is why a tiny battery can last
for weeks or months.

The trade-off is latency and throughput. A long connection interval saves power but makes
interactive printing slower.

### One connection at a time

BLE can support multiple connections in theory, but many cheap peripherals only accept one central
at a time. The MXW01 behaves this way in practice. If the phone app is connected, the laptop cannot
connect. The app must be force-quit, and sometimes phone Bluetooth must be turned off so the app
does not silently reconnect.

**Analogy:** a single-occupancy phone booth. Only one person can use it at a time. If the phone app
is already inside, the laptop waits outside.

### Pairing, bonding, and GATT connections

These are different concepts:

- **GATT connection:** you are connected to the device and can read/write characteristics.
- **Pairing:** the two sides establish encryption keys.
- **Bonding:** the keys are stored so future connections can be encrypted without pairing again.

A device can allow GATT access without pairing. The MXW01 did not require operating system
(OS)-level pairing in our tests; we connected directly to its GATT service. macOS may not list it
in the Bluetooth menu, which is normal for a BLE peripheral that does not advertise as a classic
device.

**Analogy:** entering a building (GATT connection) versus showing ID and getting a permanent access
badge (pairing/bonding). Some buildings let you walk into the lobby without a badge; the printer is
like that.

### MTU: the size of the envelope slot

**MTU** stands for Maximum Transmission Unit. In BLE, the ATT MTU is the maximum size of an ATT
packet. The default is 23 bytes. ATT uses 3 bytes of header, leaving 20 bytes for the value.

The two sides can negotiate a larger MTU. On this printer, the observed MTU was 185, and the
printer reported `max_write_without_response_size = 182` (185 − 3). That means a single write can
carry up to 182 bytes of application data.

But the MXW01 image protocol writes **48 bytes per row**. The MTU is larger, yet the driver still
clamps each write to one row. The printer’s firmware and print head expect that cadence. This is a
key lesson: **MTU is a maximum, not a target.**

**Analogy:** a mail slot that can accept a large envelope does not mean the clerk wants you to shove
a whole box through it. The slot size is a limit; the workflow has its own preferred parcel size.

### Throughput math

Throughput depends on:

- how many bytes fit in each packet (MTU),
- how many packets fit in each connection event,
- how often connection events occur (connection interval),
- protocol overhead,
- delays imposed by the device or driver.

A rough upper bound is:

```text
throughput ≈ (payload bytes per packet × packets per event) / connection interval
```

Real throughput is lower because of retries, empty events, and application pacing. The MXW01’s
observed 8–10 seconds for a short note is not a radio limitation; it is the combination of 48-byte
rows, a 10 ms delay, and the printer’s physical print speed.

**Blog angle:** “Why does a tiny receipt take ten seconds?” is a good hook for explaining that
throughput is not just bandwidth.

## 2.5 GATT: the filing cabinet

### What GATT is

**GATT** stands for **Generic Attribute Profile**. It is built on **ATT**, the **Attribute
Protocol**. GATT defines how a BLE server organizes data into **services** and **characteristics**,
and how a client discovers and accesses them.

Think of the printer as a filing cabinet:

- The **GATT server** is the cabinet.
- A **service** is a drawer.
- A **characteristic** is a folder inside the drawer.
- A **descriptor** is a label or instruction attached to the folder.
- A **UUID** is the label that says what kind of folder it is.
- A **handle** is the slot number where the folder currently sits.
- A **property** says whether you may read the folder, write to it, or be notified when it changes.

**Analogy:** a restaurant menu. The menu (GATT database) lists sections (services) and dishes
(characteristics). Each dish has a name (UUID), a price/description (descriptor), and rules about
whether you can customize it (properties). You do not need to know the kitchen’s internal layout;
you just order from the menu.

### Services

A service groups related characteristics. It has a UUID. Some services are standardized by the
Bluetooth Special Interest Group (SIG); others are vendor-specific.

Examples:

- **Generic Access Profile (GAP):** device name, appearance, preferred connection parameters.
- **Generic Attribute (GATT):** service changed indications.
- **Device Information:** manufacturer, model, firmware, serial number.
- **Battery Service:** battery level.
- **Vendor service `ae30`:** the MXW01’s command, notification, and data characteristics.

The MXW01 uses a vendor service rather than a standard printer profile. That is why a generic BLE
printer driver cannot simply “know” how to print to it.

### Characteristics

A characteristic is a value with a UUID and properties. The MXW01’s important characteristics:

| UUID suffix | Property | Purpose |
| --- | --- | --- |
| `ae01` | write-without-response | Command/control frames |
| `ae02` | notify | Responses and print-complete events |
| `ae03` | write-without-response | Raw 1-bit image stream |
| `ae04` | notify | Secondary notifications (unused) |
| `ae05` | indicate | Secondary indications (unused) |
| `ae10` | read/write | Vendor config block (unused) |

The full UUIDs use the Bluetooth Base UUID:

```text
0000ae30-0000-1000-8000-00805f9b34fb
0000ae01-0000-1000-8000-00805f9b34fb
0000ae02-0000-1000-8000-00805f9b34fb
0000ae03-0000-1000-8000-00805f9b34fb
```

The `ae30` part is the 16-bit shorthand. The rest is the standard base. This is why code can
normalize a four-character UUID into the full 128-bit form.

### Properties

Common GATT characteristic properties:

- **Read:** the client can request the current value.
- **Write:** the client can send a value and expects an ATT-level acknowledgement.
- **Write without response:** the client can send a value without an ATT-level acknowledgement.
  Faster, but no guarantee at the ATT layer.
- **Notify:** the server can push a value without the client asking each time. No ATT-level
  acknowledgement.
- **Indicate:** like notify, but the client must acknowledge at the ATT layer. More reliable, slower.
- **Broadcast:** intended for connectionless broadcasting (rare).
- **Signed write:** a write with an authentication signature.

The MXW01 uses write-without-response for commands and image data, and notify for responses. That
means the driver cannot rely on every write being acknowledged. It has to use the protocol’s own
responses and timeouts.

### Handles versus UUIDs

A **handle** is a 16-bit identifier for an attribute in the server’s attribute table. Handles are
assigned by the server and can differ between devices or even firmware versions. A **UUID** says
what the attribute *is*; a handle says *where it currently lives*.

Discovery works like this:

1. The client asks for the list of services.
2. For each service, it asks for the list of characteristics.
3. It records each characteristic’s UUID, handle, and properties.
4. It may ask for descriptors (such as the CCCD) and their handles.

The code should look up characteristics by UUID, not hard-code handles.

**Analogy:** a hotel room number (handle) versus the room type (UUID). The “king room with a
kitchen” is the type; the room number changes by hotel. You ask the front desk for the type and get
the current number.

### The CCCD: the notification subscription switch

A **Client Characteristic Configuration Descriptor (CCCD)** is a special descriptor with UUID
`00002902-0000-1000-8000-00805f9b34fb`. It controls whether the client wants notifications or
indications for a characteristic.

- Write `0x0001` to enable notifications.
- Write `0x0002` to enable indications.
- Write `0x0000` to disable.

In Bleak, `start_notify(uuid, callback)` handles the CCCD write for you. If the characteristic is
not notifiable, or the printer refuses the subscription, notifications will not arrive. The code
tolerates that for some printers because printing can still work without acknowledgements.

**Analogy:** a mailing-list signup card attached to a folder. The folder exists either way; the card
says “please send me updates when this changes.”

### GATT is not a byte stream

This is a crucial mental shift. A serial port gives you a stream of bytes. GATT gives you:

- **reads:** request a value,
- **writes:** send a value to a characteristic,
- **notifications/indications:** receive values when the server decides,
- **discovery:** enumerate services and characteristics.

Each write is a discrete ATT operation with a maximum size (MTU). If your command is larger than the
MTU, the stack fragments it at the ATT layer, but the application still thinks in terms of writes.
The printer’s firmware sees the reassembled value on the characteristic.

**Analogy:** a serial port is a hosepipe. GATT is a set of labelled mail slots. You can drop a letter
in a slot or ask the clerk for the contents of a slot; you cannot just turn on a tap.

### GATT client versus GATT server

- The **server** holds the attribute table. The printer.
- The **client** discovers and accesses it. The laptop.

The words are from the protocol’s perspective, not from a generic client/server web model. A GATT
client can write to the server; a GATT server can notify the client.

## 2.6 Notifications, indications, and the CCCD

### Notify

A **notification** is an unsolicited message from the server to the client. The client subscribes
once (via the CCCD), then the server pushes values. There is no ATT-level acknowledgement, so
notifications can be lost.

The MXW01 uses notifications on `ae02` for:

- status responses (`A1`),
- print-request acknowledgements (`A9`),
- print-complete events (`AA`),
- battery and firmware responses (`AB`, `B1`).

**Analogy:** the printer taps you on the shoulder when something happens. If you are not paying
attention, you miss the tap.

### Indicate

An **indication** is like a notification, but the client must acknowledge it at the ATT layer. The
server waits for the acknowledgement before sending the next indication. More reliable, but slower.

The MXW01 exposes an indicate characteristic (`ae05`), but the driver does not use it.

### Buffering and framing notifications

A notification is not guaranteed to contain a complete application frame. It may contain:

- part of a frame,
- multiple frames,
- a frame plus extra bytes.

The driver must append incoming bytes to a buffer and parse complete frames. The MXW01 parser:

1. Searches for the magic header `22 21`.
2. Discards bytes before it.
3. Waits until at least 6 bytes are available.
4. Reads the 16-bit length.
5. Waits until the full payload plus checksum and terminator are available.
6. Extracts the command and payload.
7. Stores the response and signals a waiting coroutine.

This is a classic **stream reassembly** problem. The same pattern appears in serial protocols, TCP,
and log parsing.

**Analogy:** receiving a multi-part letter. You keep the fragments in a tray until you have the
whole thing, then you file it. If a fragment from another letter arrives, you use the header to
separate them.

### Why notifications can be missed

- The client did not subscribe (CCCD not set).
- The printer’s firmware only notifies in certain states.
- The connection dropped.
- The notification was sent while the client was not listening.
- The printer decided the event was not important.

The driver treats a missing `AA` as “not acknowledged,” not necessarily “failed.” The physical
print may still have happened.

## 2.7 MTU, throughput, and timing

### The MTU is negotiated

The default ATT MTU is 23 bytes. Either side can request a larger MTU during connection setup. The
effective value is the minimum of what both sides support. The observed MXW01 values:

- MTU: 185 bytes.
- Maximum write-without-response payload: 182 bytes (185 − 3 bytes of ATT header).

### The application chooses a smaller write size

Even though 182 bytes fit, the MXW01 driver writes 48 bytes at a time. Why?

- The reference implementations do it.
- The printer’s firmware and buffer expect row-sized writes.
- The print head consumes one row at a time.
- Pacing avoids overflowing the printer’s buffer.

The code clamps `chunk_size` to 48:

```python
chunk_size = max(20, min(int(self.config.chunk_size or 48), 48))
delay = max(0, self.config.chunk_delay_ms) / 1000.0
for start in range(0, len(data), chunk_size):
    await self._write_chunk(data[start:start + chunk_size], uuid=self._data_uuid)
    if delay:
        await asyncio.sleep(delay)
```

### The timing math

For a 128-pixel-tall render plus 80 feed dots:

- Total rows = 128 + 80 = 208.
- Bytes = 208 × 48 = 9,984.
- Writes = 208 (one row each).
- Delay = 208 × 10 ms = 2.08 seconds.
- Plus BLE connection events, packet overhead, and the printer’s physical print time.

Observed total: about 7.9–10.1 seconds. The delay is a significant fraction, but not the whole
story. The print head and firmware also take time.

**Blog angle:** this is a concrete way to explain why “the radio is fast” does not mean “the printer
is fast.”

## 2.8 The software stack: controller, host, HCI, OS, Bleak

### Controller and host

The BLE implementation is split into two parts:

- **Controller:** the radio and link layer. It handles advertising, connections, packets, hopping,
  encryption at the link layer, and timing.
- **Host:** the upper layers: ATT, GATT, security manager, and application profiles.

The **HCI (Host Controller Interface)** is the boundary between them. It defines commands,
events, and data packets. On a laptop, the host and controller may be connected by USB, UART, or an
internal bus.

**Analogy:** the controller is the radio room; the host is the front desk. The HCI is the intercom
between them.

### Operating system APIs

Each OS exposes its own BLE API:

- **macOS:** CoreBluetooth.
- **Linux:** BlueZ, usually over D-Bus.
- **Windows:** WinRT Bluetooth APIs.

These APIs differ in details: address formats, permission prompts, caching, connection behavior,
and notification delivery. This is why cross-platform BLE code has sharp edges.

### Bleak

**Bleak** is a Python library that provides an asynchronous, cross-platform BLE client. It wraps
CoreBluetooth, BlueZ, and WinRT behind a common API:

```python
from bleak import BleakClient, BleakScanner

device = await BleakScanner.find_device_by_name("MXW01")
async with BleakClient(device) as client:
    await client.write_gatt_char(AE01, frame, response=False)
    await client.start_notify(AE02, on_notify)
```

Bleak handles discovery, connection, GATT enumeration, notification subscription, and writes. It
does not know anything about the MXW01 protocol; that is our code’s job.

**Analogy:** Bleak is a universal remote that can talk to many BLE devices. It knows how to press
the buttons, but not what the buttons mean for a particular device.

### Why BLE startup is flaky

Common causes:

- The OS Bluetooth stack is still starting.
- The user has not granted Bluetooth permission to the terminal or editor.
- The device is asleep or connected to another central.
- The OS caches a stale device entry.
- The adapter is busy or in a bad state.
- The device’s address changed (random address).

The service auto-connect retries three times with a 1.5-second delay. The CLI and web UI also offer
manual reconnect. Retries belong in the driver because BLE startup is inherently asynchronous and
stateful.

**Analogy:** waking up a sleeping shopkeeper. You may need to knock more than once, and the first
knock may happen before they are awake.

## 2.9 What this means for the MXW01

Putting the BLE primer together:

- The printer is a BLE peripheral and a GATT server.
- It advertises as `MXW01`, but the advertised service `af30` is not the command service.
- After connecting, the real service is `ae30`.
- Commands go to `ae01`; notifications come from `ae02`; image data goes to `ae03`.
- The characteristics are write-without-response and notify, so acknowledgements are
  protocol-level, not link-level.
- The MTU is 185, but the image protocol uses 48-byte rows with a 10 ms delay.
- The printer accepts one central at a time and sleeps when idle.
- macOS exposes a CoreBluetooth UUID, not a MAC address; use the name `MXW01`.

Everything else in this document builds on those facts.

## 2.10 Common misconceptions

- **“BLE is just Bluetooth with less power.”** It is a different link layer and protocol stack
  with different roles, advertising, and GATT. Classic Bluetooth audio and BLE sensors are not
  interchangeable.
- **“The advertised service is the API.”** No. Connect and enumerate GATT.
- **“A write means the device received it.”** Write-without-response has no ATT-level
  acknowledgement. The application protocol must confirm.
- **“A notification means the action succeeded.”** It may mean “received,” “started,” “finished,”
  or “error,” depending on the command.
- **“MTU is throughput.”** MTU is the maximum packet size. Throughput depends on connection
  interval, packets per event, overhead, and device pacing.
- **“Pairing is required for BLE.”** Not always. GATT can be open; pairing is a security feature.
- **“The UUID is the MAC address.”** On macOS, CoreBluetooth UUIDs are not MAC addresses.
- **“The printer is stateless.”** It has paper, cover, battery, temperature, sleep, and a single
  connection. Physical state is part of the API.

---

# Part 3 — The MXW01: discovering and speaking its protocol

## 3.1 The hardware: a hot wire on thermal paper

### What “thermal printer” means

A thermal printer does not use ink. The paper is coated with a mixture that turns dark when heated.
A **thermal print head** contains a row of tiny heating elements, one per dot. The firmware decides
which elements to energize for each row, and the paper moves past the head.

**Analogy:** a row of tiny irons stamping a pattern onto heat-sensitive paper. Each iron is either
on or off for a given row; the paper’s coating does the rest.

This explains several constraints:

- **No color** in monochrome mode: each dot is heated or not.
- **No true grayscale** in 1-bit mode: you can approximate gray with patterns of black dots
  (dithering), or use a printer mode that supports multiple energy levels.
- **Paper matters:** the smooth, heat-sensitive side must face the head. Cheap or old paper may
  print faintly.
- **Density matters:** more energy makes darker dots, but too much can smear or shorten head life.
- **Speed matters:** the head and paper need time to transfer heat.

### The numbers

| Property | Value | Meaning |
| --- | --- | --- |
| Print head width | 384 dots | 384 pixels per row |
| Resolution | 203 dots per inch (dpi) | 203 dots per inch, about 8 dots per millimeter |
| Printable width | ~48 mm | 384 / 8 = 48 mm |
| Paper width | ~57 mm | Wider than the printable area; margins are physical |
| Rows per byte | 48 bytes | 384 / 8 = 48 |
| Color | 1-bit | Black or white per dot in mono mode |

**DPI** stands for dots per inch. At 203 dpi, one millimeter contains about 8 dots. A 16-pixel font
is about 2 mm tall. This is why receipt typography is chunky and why small text can turn to mush.

### The print head and the row buffer

The firmware receives image rows, stores them in a buffer, and shifts bits into the print head’s
shift registers. A **strobe** signal energizes the selected heating elements for a precise amount of
time. Then the paper advances one row, and the process repeats.

**Analogy:** a dot-matrix display refreshed one row at a time. The printer cannot “draw” a whole
page at once; it burns one line, moves the paper, and burns the next.

### Sensors and state

The printer also has:

- a **paper sensor** (paper present or empty),
- a **cover sensor** (lid open or closed),
- a **battery gauge**,
- a **thermistor** for temperature,
- a **power/feed button**,
- firmware that manages sleep and charging.

These are not optional details. They are part of the device’s state machine, and they affect
whether a print job will succeed.

### USB-C

On this class of printer, the USB-C port is usually **charging only**. The product’s data interface
is Bluetooth. `thermal usb-probe` listed only the Mac’s debug console and Bluetooth incoming port;
no USB serial device appeared. The probe should be repeated with the cable connected to confirm,
but the practical assumption is power-only.

If a device does expose a serial interface, the project has an Epson Standard Code for Point of
Sale (ESC/POS)-over-serial transport. That is a fallback, not the main path.

## 3.2 Discovery: the advertisement lied, the GATT table told the truth

### The scan

`thermal scan` showed:

```text
Scanning BLE for 8s...
* MXW01   6D717764-D3E3-2239-C52D-42759E25147F   -55 dBm
    services: 0000af30-0000-1000-8000-00805f9b34fb
```

The name `MXW01` is correct. The long identifier is a macOS CoreBluetooth UUID, not a MAC address.
The advertised service is `af30`.

### The GATT table after connecting

The actual command service is `ae30`, not `af30`. The verified layout:

| UUID | Properties | Purpose |
| --- | --- | --- |
| `0000ae30-...` service | — | Vendor command service |
| `0000ae01-...` | write-without-response | Command/control frames |
| `0000ae02-...` | notify | Responses and print-complete events |
| `0000ae03-...` | write-without-response | Raw 1-bit image stream |
| `0000ae04-...` | notify | Secondary notifications (unused) |
| `0000ae05-...` | indicate | Secondary indications (unused) |
| `0000ae10-...` | read/write | Vendor config block (unused) |

There is also a secondary service `0000ae3a-...` with `ae3b` (write) and `ae3c` (notify). The
driver uses `ae30`.

**Analogy:** the sign in the shop window says “Service entrance around the side,” but the actual
service counter is through the front door. The sign is a hint; the building directory inside is
authoritative.

### How the driver resolves the characteristics

The MXW01 transport:

1. Connects with Bleak.
2. Iterates over every service and characteristic.
3. Records UUIDs and properties.
4. Checks whether the `ae30` service or the `ae01`/`ae03` characteristics exist.
5. If it sees the LuckPrinter layout (`ff00`/`ff02`) instead, raises `ProtocolMismatch` with a
   suggestion to use the `luck` protocol.
6. Chooses `ae01` for commands, `ae02` for notifications, and `ae03` for image data.
7. Allows config overrides (`service_uuid`, `write_uuid`, `notify_uuid`, `data_uuid`) for variants.

This is more robust than hard-coding handles. UUIDs are the stable names; handles are assigned by
the server.

### Why the advertisement can differ

Advertising data is small and controlled by the firmware. It may list a service that is not the
command service, omit services, or include a scan-response service list. The only authoritative
source is the GATT table after connection.

**Blog angle:** “The device lied to me” is memorable, but the precise lesson is “advertising is a
hint; GATT is the contract.”

## 3.3 The frame format: a container with a manifest and a seal

### The format

```text
22 21 <command> 00 <length LE16> <payload> <crc8> FF
```

`LE16` means a 16-bit unsigned integer in little-endian order: the low byte comes first, then the
high byte. For example, 208 = `0x00D0` is written `D0 00`.

| Field | Size | Meaning |
| --- | --- | --- |
| `22 21` | 2 bytes | Magic header. Helps the receiver find the start of a frame. |
| `<command>` | 1 byte | Command code, e.g. `A1`, `A9`, `AD`. |
| `00` | 1 byte | Reserved/flag byte. The driver always sends `00`. |
| `<length LE16>` | 2 bytes | Payload length only, little-endian. |
| `<payload>` | N bytes | Command-specific data. |
| `<crc8>` | 1 byte | CRC-8 over the payload only. |
| `FF` | 1 byte | Terminator. |

Important details:

- The length does **not** include the header, CRC, or terminator. It counts only the payload.
- The CRC covers only the payload, not the header or length.
- The terminator is a fixed `FF`.
- The frame is sent to `ae01` for commands; image data is sent to `ae03` without this frame
  wrapper.

**Analogy:** a shipping container with a standard color (magic), a destination label (command), a
manifest count (length), the cargo (payload), a tamper seal (CRC), and a known closing latch
(terminator). The port can find the container, check the seal, and know where the cargo ends.

### Why both a length and a terminator?

The length tells the receiver how many bytes to read. The terminator gives a second signal that the
frame ended. If the length is corrupted, the terminator can help the receiver resynchronize. If the
terminator is corrupted, the length still bounds the payload. Redundancy is useful in a noisy
wireless link.

### Exact frames sent by the driver

Status query:

```text
22 21 A1 00 01 00 00 00 FF
```

Decoded:

- `22 21` magic
- `A1` status command
- `00` reserved
- `01 00` payload length = 1
- `00` payload
- `00` CRC of payload `00`
- `FF` terminator

Firmware query:

```text
22 21 B1 00 01 00 00 00 FF
```

Battery query:

```text
22 21 AB 00 01 00 00 00 FF
```

Intensity 93 (`0x5D`):

```text
22 21 A2 00 01 00 5D 94 FF
```

Print request for 208 rows, 48 bytes per row, monochrome:

```text
22 21 A9 00 04 00 D0 00 30 00 34 FF
```

Flush/end of data:

```text
22 21 AD 00 01 00 00 00 FF
```

The printer replies on `ae02` with frames using the same `22 21` header. A print-complete event is
`AA`.

### Response parsing

The notification parser is a small stream reassembler:

```python
def _drain_frames(self) -> None:
    while True:
        start = self._buffer.find(protocol.MXW01_HEADER)
        if start < 0:
            if len(self._buffer) > 1:
                del self._buffer[:-1]
            return
        if start:
            del self._buffer[:start]
        if len(self._buffer) < 6:
            return
        length = int.from_bytes(self._buffer[4:6], "little")
        end = 6 + length
        if len(self._buffer) < end:
            return
        command = self._buffer[2]
        payload = bytes(self._buffer[6:end])
        del self._buffer[:end]
        self._responses[command] = payload
        self._event.set()
```

Notice:

- It searches for the magic header and discards garbage before it.
- It keeps a possible partial header for the next packet.
- It waits for the full length before parsing.
- It stores responses by command code.
- It signals an `asyncio.Event` so a waiting coroutine can wake up.

This is the same pattern used in serial protocols and TCP stream parsing.

### Command map

| Command | Direction | Payload | Meaning |
| --- | --- | --- | --- |
| `A1` | host → printer | `00` | Get status; printer replies with `A1` |
| `A2` | host → printer | intensity `00`–`64` | Set print intensity |
| `A3` | host → printer | lines LE16 | Eject/feed paper |
| `A4` | host → printer | lines LE16 | Retract paper |
| `A7` | host → printer | `00` | Query print count |
| `A9` | host → printer | height LE16, `30`, mode | Print request |
| `AA` | printer → host | — | Print complete |
| `AB` | host → printer | `00` | Battery level |
| `AC` | host → printer | — | Cancel print |
| `AD` | host → printer | `00` | End of image data / flush |
| `B0` | host → printer | `00` | Get print type |
| `B1` | host → printer | `00` | Get firmware version |

Only a subset is exposed by the CLI/UI: status, intensity, print, flush, battery, and version.
`A3`, `A4`, `A7`, `AC`, and `B0` are documented for completeness but not used in the main flow.

## 3.4 CRC-8: the mathematical tamper seal

### What a CRC is

A **Cyclic Redundancy Check (CRC)** treats the data as a polynomial over the integers modulo 2 and
divides it by a fixed polynomial. The remainder is the checksum.

You do not need to do polynomial long division by hand to use it. The bitwise algorithm is:

```python
def mxw01_crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc
```

Parameters:

- Polynomial: `0x07`.
- Initial value: `0x00`.
- Input reflection (`refin`): no.
- Output reflection (`refout`): no.
- Final exclusive OR (XOR) (`xorout`): `0x00`.
- Coverage: payload only.

This is often called **CRC-8/SMBus**. The check value for the ASCII string `123456789` is `0xF4`.

**Analogy:** a CRC is a mathematical tamper seal. It does not tell you what the cargo is, but if
the cargo changes, the seal almost certainly changes too. A simple sum is a weak seal; a CRC is a
much better one.

### Why “CRC-8” is ambiguous

There are many CRC-8 variants. They differ in:

- polynomial,
- initial value,
- whether input bits are reflected,
- whether output bits are reflected,
- final XOR,
- what data is covered.

The **Dallas/Maxim** variant uses a reflected polynomial and produces `0xA1` for `123456789`. The
MXW01 uses the non-reflected `0x07` variant, which produces `0xF4`. Both are legitimately “CRC-8,”
but they are not interchangeable.

**Blog angle:** this is a great example of why protocol documentation must specify every parameter,
not just the name.

### Worked examples

Payload `0x5D`:

```text
CRC-8(5D) = 94
```

So the intensity frame is:

```text
22 21 A2 00 01 00 5D 94 FF
```

A9 payload `D0 00 30 00`:

```text
CRC-8(D0 00 30 00) = 34
```

So the print request is:

```text
22 21 A9 00 04 00 D0 00 30 00 34 FF
```

Payload `00`:

```text
CRC-8(00) = 00
```

That is why the status, battery, firmware, and flush frames end in `00 00 FF`.

### What happens with a wrong CRC

The printer may silently ignore the frame. There is no guarantee of a helpful error. From the
laptop, it looks like the printer did nothing. This is why known-answer tests matter: a frame can
look plausible and still be rejected.

### The `00 00` variant

Some reference implementations use `00 00` instead of `crc8 FF` for `A9` and `AD`. The hardware
accepts both. This driver uses the CRC form consistently because it is easier to validate and
debug. If you are porting code, do not assume every implementation uses the same trailer.

## 3.5 Commands and status: the printer’s dashboard

### A1 status

The status query is `A1 00`. The printer replies with an `A1` notification whose payload contains
fixed offsets:

| Offset | Meaning |
| --- | --- |
| 0 | State: `0` standby, `1` printing, `2` feeding, `3` ejecting |
| 3 | Battery percentage (0–100) |
| 4 | Temperature in °C |
| 6 | Status: `0` = OK |
| 7 | Error code when status is non-zero |

Error codes:

| Code | Meaning |
| --- | --- |
| `01`, `09` | No paper |
| `04` | Overheated |
| `08` | Low battery |

Real payload from the unit:

```text
00 00 00 5A 14 00 00 00 F3 00
```

Decoded:

- offset 0 = `00` → standby
- offset 3 = `5A` → 90% battery
- offset 4 = `14` → 20 °C
- offset 6 = `00` → OK
- offset 7 = `00` → no error
- extra bytes `F3 00` are unused

The payload has extra bytes; the parser only relies on the documented offsets and checks lengths
before indexing.

### AB battery

The battery query returns a payload whose first byte is the percentage (0–100). The parser returns
`None` if the value is out of range.

### B1 firmware version

The version payload is a UTF-8 string, often null-terminated. The parser splits at the first null
byte, decodes UTF-8 with replacement, and strips non-printable characters. The verified unit
reported `1.9.3.1.1`.

### A2 intensity

The payload is one byte from 0 to 100. The UI’s density levels map to:

| UI density | Printer value |
| --- | --- |
| 0 (light) | `0x3C` (60) |
| 1 (normal) | `0x5D` (93) |
| 2 (dark) | `0x64` (100) |

The driver may send `A2` more than once; the printer accepts it.

### A9 print request

Payload:

```text
height low, height high, 0x30, mode
```

- Height is the total number of rows, including feed rows.
- `0x30` = 48 bytes per row.
- Mode `0x00` = monochrome.
- Mode `0x01` = mono with less post-print feed.
- Mode `0x02` = 4bpp grayscale (documented but not implemented here).

The printer replies with an `A9` acknowledgement. A first payload byte of `0` means accepted; a
non-zero value is an error. The driver tolerates a missing acknowledgement because some firmware
revisions only notify while printing.

### AA print complete

`AA` is a notification, not a command you send. It means the physical print finished. The driver
waits for it with a timeout of `max(15, height / 20)` seconds. If it does not arrive, the job is
reported as `acked: false`, but the print may still have happened.

**Analogy:** the printer’s “order up” bell. If the bell is broken, the food may still be on the
counter.

### AD flush

`AD 00` tells the printer that the image data is complete and it should flush/print. Without it, the
printer may wait for more rows.

### Why acks are ambiguous

A protocol acknowledgement can mean different things:

- “I received the bytes.”
- “I parsed the command.”
- “I accepted the job.”
- “I finished the physical print.”

The MXW01 uses different commands for these: `A9` is the print request ack, `AA` is the completion
event. The driver must not conflate them.

## 3.6 The print sequence, end to end

### The sequence

```text
1. A1 00                                  status check
2. A2 5D                                  intensity
3. A9 <height LE16> 30 00                 print request; wait for A9 ack
4. <image rows>                           stream to AE03, 48 bytes per row
5. AD 00                                  end of data / flush
6. wait for AA                            print complete
```

### Why each step exists

1. **Status check:** avoids printing with no paper, an open cover, low battery, or overheating. If
   the printer does not answer, the driver continues because some firmware revisions are quiet.
2. **Intensity:** sets the heat energy. Too low is faint; too high is dark or smeared.
3. **Print request:** tells the printer how many rows to expect and how they are encoded. The
   printer can reject the job before the data arrives.
4. **Image data:** the actual raster, one row at a time.
5. **Flush:** tells the printer the stream is complete.
6. **Completion:** waits for the physical print to finish so the user gets a meaningful result.

### The code path

```python
status_payload = await self._query(protocol.MXW01_CMD_STATUS, timeout=4.0)
status = protocol.parse_mxw01_status(status_payload or b"")
if status is not None and not status.ok:
    raise TransportError(f"Printer reports: {status.describe()}")

intensity = protocol.mxw01_intensity(...)
await self._send_frame(protocol.MXW01_CMD_INTENSITY, bytes([intensity]))

request = height.to_bytes(2, "little") + bytes([width_bytes, 0x00])
ack = await self._query(protocol.MXW01_CMD_PRINT, request, timeout=5.0)
if ack and ack[0] != 0:
    raise TransportError(f"Printer rejected the print request (status {ack[0]})")

await self._write_data(data)
await self._send_frame(protocol.MXW01_CMD_FLUSH, b"\x00")

timeout = max(15.0, height / 20.0)
complete = await self._wait_for(protocol.MXW01_CMD_PRINT_COMPLETE, timeout)
```

### Timing, with numbers

For a 128-pixel render plus 80 feed dots:

- rows = 208
- bytes = 208 × 48 = 9,984
- writes = 208
- delay = 208 × 10 ms = 2.08 s
- observed total = 7.9 s (web/daemon) to 10.1 s (direct CLI)

The difference between direct and daemon is mostly connection setup and process overhead, not the
protocol itself. The first print may also include scanning and connection time.

### Why not send bigger chunks?

The MTU allows 182 bytes per write. The driver clamps to 48 because:

- the printer expects row-sized writes,
- the print head consumes rows,
- the printer’s buffer is small,
- the reference implementations use this cadence.

Sending larger chunks might work sometimes, but it is less predictable. The protocol’s pacing is
part of the contract.

**Analogy:** a restaurant kitchen can accept a large delivery, but it wants ingredients prepped one
tray at a time. Delivering a truckload at once does not make dinner arrive faster.

## 3.7 The raster format: 384 tiny switches per row

### The basics

- Width: 384 pixels.
- Bytes per row: 384 / 8 = 48.
- Bits per pixel: 1.
- Bit order: **LSB-first** (least significant bit first).
- Polarity: **1 = black**, **0 = white**.
- Rows are contiguous; no per-row header or padding beyond 48 bytes.
- Data is written to `ae03`.

### What LSB-first means

Within each byte, the first pixel in the row is bit 0 (the least significant bit), not bit 7. So
for an 8-pixel row:

| Pixels (left to right) | Binary bits (bit 7 … bit 0) | Byte |
| --- | --- | --- |
| `1000 0000` (leftmost black) | `0000 0001` | `01` |
| `0000 0001` (rightmost black) | `1000 0000` | `80` |
| all black | `1111 1111` | `FF` |
| all white | `0000 0000` | `00` |

**Analogy:** reading a byte’s bits from right to left while placing pixels from left to right. It
feels backwards, which is exactly why bit-order bugs are common.

### The Pillow mismatch

Pillow’s 1-bit image mode (`"1"`) uses:

- **0 = black**, **1 = white** (opposite of the printer),
- **most-significant-bit (MSB)-first** packing (opposite of the printer).

So the driver cannot simply call `image.tobytes()`. It must invert the polarity and reverse the bit
order:

```python
_REVERSE_BITS = tuple(int(f"{value:08b}"[::-1], 2) for value in range(256))

raw = image.tobytes()
return bytes(_REVERSE_BITS[255 - value] for value in raw)
```

- `255 - value` flips every bit, changing Pillow’s 0=black to 1=black.
- `_REVERSE_BITS[...]` reverses the bit order, changing MSB-first to LSB-first.

A lookup table is used because it is fast and easy to test. The tests cover leftmost black,
rightmost black, all black, and all white.

### Worked example: one row

Suppose the first 8 pixels are black, white, black, white, black, white, black, white. In printer
terms that is `1 0 1 0 1 0 1 0` from left to right. LSB-first means bit 0 is the leftmost pixel, so
the byte is `0101 0101` in bit order 7…0, which is `0x55`.

### Feed rows

Paper feed is not a separate “margin” command in the normal path. The driver appends blank rows
(48 zero bytes per row) to the image data and includes them in the `A9` height.

Why? The printer’s `A3` eject command has less predictable feed distance, and the image height is
the contract for how many rows to print. Appending blank rows makes the feed deterministic.

**Analogy:** the printer does not understand “leave a 2 cm margin.” It understands “print 208 rows,
and the last 80 are blank.” The margin is part of the picture.

### Width handling

`mxw01_image_data`:

- clamps the width to a multiple of 8, up to 384,
- resizes images wider than the target width with LANCZOS,
- pastes narrower images onto a white canvas,
- converts to 1-bit if needed.

For a 384-pixel image, each row is exactly 48 bytes. For a narrower image, the row is still padded
to the target width so the printer’s row stride stays consistent.

### 4bpp grayscale

Mode `0x02` is documented as 4 bits per pixel grayscale. It would allow more tones, but it is not
implemented here. The monochrome path is simpler, faster, and sufficient for receipts.

## 3.8 Fallback protocols and auto-detection

### Why support more than one protocol?

The same physical printer can be sold under many names and use different firmware. The project
supports:

- **MXW01** (primary),
- **LuckPrinter / DP-L1S** (fallback),
- **ESC/POS** over serial or a generic BLE bridge,
- **mock** for development.

The renderer and UI are protocol-agnostic. Only the transport and protocol builder change.

### LuckPrinter / DP-L1S

GATT layout:

| Service | Write | Notify |
| --- | --- | --- |
| `0000ff00-...` | `0000ff02-...` | `0000ff01-...` |

The driver sends:

1. A LuckPrinter preamble: `10 FF F1 03` (enable) plus twelve null wake bytes.
2. A standard ESC/POS `GS v 0` raster image (384 px = 48 bytes per row, MSB-first).
3. A feed command.
4. A stop command: `10 FF F1 45`.

The LuckPrinter command set uses Data Link Escape (DLE) (`0x10`) and `FF` (`0xFF`) prefixes. Status
is a bit field: printing, cover open, paper empty, low battery, overheating, charging.

### ESC/POS

**ESC/POS** is a family of command sequences used by many receipt printers. It uses control bytes:

- `ESC` = `0x1B`,
- `Group Separator (GS)` = `0x1D`,
- `DLE` = `0x10`.

The raster command is `GS v 0`:

```text
1D 76 30 00 <width bytes LE16> <height LE16> <bitmap>
```

ESC/POS is **MSB-first** and uses `1 = black`. Pillow’s packed bytes are already MSB-first, so the
driver only needs to invert the polarity, not reverse the bit order.

### Auto-detection

With `protocol = "auto"`:

1. The manager creates the default BLE transport.
2. It connects and inspects the GATT layout.
3. If the layout belongs to the other family, the transport raises `ProtocolMismatch` with a
   suggested protocol.
4. The manager disconnects, recreates the transport with the suggested protocol, and retries once.

This is a clean example of **dependency inversion**: the transport knows how to identify itself, and
the manager knows how to retry. Neither needs a giant `if` statement in the UI.

**Analogy:** a receptionist tries the main door, discovers it is the wrong department, and walks you
to the correct one. You do not have to know the building layout in advance.

---

# Part 4 — From text to dots: rendering

## 4.1 The display constraint

The printer is a 384-pixel-wide, 1-bit display with no color and no true grayscale in mono mode. It
is also physically small: about 48 mm of printable width. That changes design:

- A sentence that looks fine on a laptop may wrap into many lines.
- A thin font may disappear when thresholded.
- A photo needs dithering to suggest shades of gray.
- A QR code needs crisp module edges.
- A receipt is read at arm’s length, not on a high-DPI screen.

**Analogy:** designing for a thermal printer is like designing a poster for a fax machine. You have
one color, low resolution, and a fixed width. Constraint is the medium.

### Pixels, dots, and DPI

A **pixel** is a picture element in software. A **dot** is a physical mark on paper. At 203 dpi,
one dot is about 0.125 mm. A 16-pixel font is about 2 mm tall. A 384-pixel row is about 48 mm wide.

The printer’s resolution is fixed. The renderer works in pixels and lets the printer map pixels to
dots one-to-one.

## 4.2 The rendering pipeline

```text
Input: message, title, style, footer, QR, image, width, density, feed
  ↓
1. Sanitize text
   - Unicode normalization
   - emoji/symbol substitutions
  ↓
2. Parse markdown-lite
   - headings, paragraphs, bullets, tasks, quotes, code, rules
   - inline bold, italic, code
  ↓
3. Lay out runs
   - choose fonts by role and size
   - measure text in pixels
   - greedy word wrap
  ↓
4. Draw to a grayscale canvas
   - 384 px wide, margins, line heights
   - rules, boxes, QR, image
  ↓
5. Threshold or dither
   - text/line art: threshold at 170
   - photos: Floyd–Steinberg before 1-bit conversion
  ↓
6. Crop and convert to 1-bit
  ↓
7. Protocol layer packs rows
   - MXW01: LSB-first, 1 = black
   - ESC/POS: MSB-first, 1 = black
```

### Why a grayscale canvas?

Pillow can draw antialiased text and images in grayscale (`"L"` mode). The renderer draws in
grayscale, then reduces to 1-bit at the end. This gives better-looking text than drawing directly
in 1-bit mode, because the threshold can make antialiased edges crisp.

### Why threshold text but dither photos?

- **Text:** thresholding preserves sharp edges. Dithering small text makes it fuzzy and hard to
  read.
- **Photos:** dithering approximates gradients by mixing black and white dots. Thresholding a photo
  would produce large flat black/white regions and lose detail.

**Analogy:** text is a line drawing; you want clean ink. A photo is a continuous tone; you want a
pattern of dots that tricks the eye into seeing gray.

## 4.3 Fonts and typography

### Font files and sizes

The renderer uses TrueType font files from the system:

- sans: Arial, Helvetica, SF, DejaVu Sans, Liberation Sans,
- sans bold/italic: matching variants,
- mono: SF Mono, Menlo, Courier New, Consolas, DejaVu Sans Mono,
- serif: Georgia, Times New Roman, DejaVu Serif, Liberation Serif.

If a font is missing, the `FontBook` falls back to a related role and finally to Pillow’s default
font. Fonts are cached by role and size.

**Analogy:** a font is a set of metal letter stamps. The size is the height of the stamp; the role
is the style (serif, sans, mono). If a stamp is missing, you substitute the closest one.

### Font metrics: ascent, descent, baseline, x-height

Text layout is not just “draw at (x, y).” Fonts have metrics:

- **Baseline:** the invisible line on which most letters sit.
- **Ascent:** the distance from the baseline to the top of the font’s tallest characters.
- **Descent:** the distance from the baseline to the bottom of characters like `g`, `p`, `y`.
- **x-height:** the height of lowercase letters like `a`, `c`, `e` (no ascenders).
- **Line height / leading:** the vertical distance between baselines.

**Analogy:** ruled notebook paper. The baseline is the printed line. Letters with ascenders (`h`,
`b`, `d`) reach above it; letters with descenders (`g`, `p`, `y`) hang below it; letters like `a`
sit between the baseline and the x-height.

### Why baseline alignment matters

If you align the **tops** of words, a word without ascenders (`aaa`) sits lower than a word with
ascenders (`haa`). The tops of their bounding boxes differ, but their baselines should match. This
was the source of the misalignment bug (section 4.5).

### Measuring text

`_measure` uses Pillow’s `draw.textlength(text, font=font)`, which returns the width in pixels. If
that fails, it falls back to an estimate based on character count and font size.

The renderer measures in pixels, not characters, because proportional fonts have different widths
for different characters. A space is not a fixed unit of alignment.

## 4.4 Layout, wrapping, and styles

### Greedy word wrap

`_layout_runs` walks through words and spaces, measuring each candidate line. If adding a word
would exceed the maximum width, it starts a new line. If a single word is wider than the line, it
breaks the word by character.

**Analogy:** packing words into a box. You keep adding words until the next one would stick out,
then you start a new row. If one word is longer than the box, you cut it into pieces.

### Styles

| Style | Design | Use case |
| --- | --- | --- |
| `note` | Title, rule, sans body, bullets/tasks | General messages, build results |
| `receipt` | Monospace, centered title, dashed rules, two-column lines | Itemized summaries, costs, before/after |
| `quote` | Serif, large type, centered, quotation mark | Encouragement, memorable lines |
| `task` | Checkboxes, title, date | To-do lists, standup notes |
| `sticker` | Auto-sized bold text in a rounded border | Big short messages, labels |

The renderer also supports:

- headings (`#`, `##`, `###`),
- bold (`**text**`), italic (`*text*`), inline code (`` `text` ``),
- bullets (`-`, `*`, `+`),
- numbered lists (`1.`, `2.`),
- task items (`[x]`, `[ ]`),
- block quotes (`>`),
- fenced code blocks,
- horizontal rules (`---`, `***`).

### Margins and line height

The default margin is 18 pixels; the quote style uses 22. Body text is 16 px with a 22 px line
height; titles are larger. The canvas grows dynamically up to a maximum height, then crops to the
used area.

**Analogy:** margin is the white space around the text; line height is the spacing between ruled
lines. Too tight and the receipt feels cramped; too loose and it wastes paper.

## 4.5 The baseline bug: why `aaa` sat higher than `haa`

### Symptom

Words without ascenders (`aaa`) sat higher than words with ascenders (`haa`). Mixed bold, italic,
and code runs looked jittery. The font was fine; the alignment was wrong.

### Cause

Pillow’s `anchor="la"` anchors a text run to the **left ascender** of its own glyph bounding box.
That is top alignment, not baseline alignment. A word like `aaa` has no ascenders, so its bounding
box top is lower than the top of `haa`. Drawing both at the same `y` makes their baselines differ.

**Analogy:** aligning the tops of letters instead of the feet. A tall letter and a short letter
start at the same height, so the short letter’s feet float above the line.

### Fix

Compute a shared baseline from font metrics and draw every run with `anchor="ls"` (left baseline):

```python
baseline = canvas.y + max(_ascent(run.font) for run in line)
for run in line:
    canvas.text(cursor, run.text, run.font, anchor="ls", baseline=baseline)
    cursor += _measure(canvas.draw, run.text, run.font)
```

`_ascent(font)` uses `font.getmetrics()` so the ascent is font-constant, not glyph-dependent:

```python
def _ascent(font):
    try:
        ascent, _descent = font.getmetrics()
        return int(ascent)
    except Exception:
        return int(max(1, getattr(font, "size", 8)) * 0.9)
```

### Regression test

`test_words_share_a_baseline` renders two words, finds their ink columns, groups them, and asserts
their bottom rows are equal. This catches the bug even if the font changes.

### Lesson

Typography bugs that look like “the font is wrong” are often coordinate or anchor bugs. Measure the
font’s metrics; do not guess from the glyph bounding box.

## 4.6 Receipt columns: measure pixels, not spaces

### Symptom

Amounts in receipt lines did not line up on the right edge.

### Cause

Padding with spaces assumes a fixed-width font and consistent character widths. The receipt style
uses a monospace font, but mixed runs and proportional fallback fonts break the assumption. Even
in monospace, different font sizes change the pixel width of a space.

### Fix

Split on **two or more spaces**, measure the right column in pixels, and draw it at the right edge:

```python
match = re.match(r"^(.*?)\s{2,}(\S.*)$", line)
if match and _measure(canvas.draw, line, font) <= canvas.content_width:
    left, right = match.group(1), match.group(2)
    canvas.text(canvas.margin, left, font)
    rw = _measure(canvas.draw, right, font)
    canvas.text(canvas.right - int(rw), right, font)
```

If the line is too wide, fall back to normal wrapping.

**Analogy:** using a ruler to place the amount at the right edge, instead of counting spaces and
hoping the letters are the same width.

### Lesson

In proportional typography, characters are not columns. Measure the rendered pixels.

## 4.7 Images, QR codes, and dithering

### Image preparation

`prepare_image`:

1. Applies EXIF orientation (photos from phones are often rotated).
2. Composites transparent images onto a white background.
3. Converts to grayscale.
4. Optionally inverts.
5. Applies autocontrast.
6. Resizes to the target width with LANCZOS.
7. Converts to 1-bit with Floyd–Steinberg dithering.

**EXIF** is the metadata block in image files that can include orientation, camera settings, and
timestamps. Ignoring it can print a sideways photo.

### Dithering

**Floyd–Steinberg dithering** distributes the quantization error of each pixel to neighboring
pixels. The result is a pattern of black and white dots that approximates gray.

**Analogy:** a newspaper photo. Up close, it is just black dots; from a distance, the dots blend
into shades.

### QR codes

The QR path:

1. Encodes the text with the `qrcode` library.
2. Uses error correction level M (about 15% recovery).
3. Renders a small module grid.
4. Resizes with **nearest-neighbor** interpolation to keep module edges crisp.
5. Draws it on the canvas.

Smoothing a QR code with bilinear or LANCZOS interpolation blurs the module boundaries and can make
it unscannable. Nearest-neighbor preserves hard edges.

**Analogy:** a QR code is a grid of black and white tiles. Resizing with smoothing is like painting
the tiles with an airbrush; the scanner cannot tell where one tile ends and the next begins.

### Symbols and emoji

Bitmap fonts may not have glyphs for emoji or unusual symbols. The renderer maps common ones to
ASCII:

- `✅` → `[x]`
- `❌` → `[ ]`
- `→` → `->`
- `←` → `<-`
- `•` → `-`
- `…` → `...`
- `—`, `–`, `−` → `-`
- `×` → `x`

This keeps the output predictable instead of printing missing-glyph boxes.

## 4.8 Why rendering is the product

The radio and protocol are commodity engineering. The difference between a useful receipt and an
unreadable smudge is the renderer:

- font choice and size,
- margins and line height,
- baseline alignment,
- wrapping,
- threshold vs dither,
- QR module sharpness,
- feed and paper handling.

**Blog angle:** “The hard part was not the Bluetooth; it was making 384 pixels look good.”

---

# Part 5 — The software: architecture and code

## 5.1 What the program must do

At minimum, the software must:

1. Find the printer over BLE.
2. Connect and discover its GATT layout.
3. Turn a message into a 1-bit image.
4. Turn the image into the printer’s raster format.
5. Send the correct command sequence.
6. Wait for acknowledgements or time out gracefully.
7. Report status, battery, and errors.
8. Let a human or another program trigger a print.
9. Remember the device and settings.
10. Keep a preview/history so failures are debuggable.

The design question is not “how do I write a script?” It is “where should each responsibility
live so the thing can be tested, reused, and debugged?”

## 5.2 Language and tooling

### Python

Python was chosen because:

- Bleak provides cross-platform BLE access.
- Pillow is excellent for image rendering and 1-bit conversion.
- FastAPI makes a local HTTP API quick to build.
- The code is readable and easy to modify.
- The target user (a developer with a coding agent) likely has Python.

The trade-offs:

- Python is slower than C/Rust for pixel-level work, but Pillow’s C internals handle the heavy
  lifting.
- Packaging and virtual environments can be confusing.
- Async BLE APIs require care.

### `uv`

**uv** is a Python project and package manager. It:

- reads `pyproject.toml`,
- resolves dependencies,
- writes/uses `uv.lock` for reproducible installs,
- creates a virtual environment (`.venv`),
- runs commands inside that environment with `uv run`.

`uv sync` installs the project and its dependencies. `uv run thermal ...` runs the `thermal` entry
point without manually activating the virtual environment.

**Analogy:** `uv` is a workshop manager. It gathers the right tools (dependencies), keeps them in a
labelled toolbox (`.venv`), and runs your command with that toolbox on the bench.

### Dependencies

From `pyproject.toml`:

| Package | Role |
| --- | --- |
| `bleak` | Cross-platform BLE client |
| `fastapi` | HTTP API framework |
| `pillow` | Image rendering and 1-bit conversion |
| `pyserial` | Serial port support for ESC/POS fallback |
| `python-multipart` | Form/file upload support for FastAPI |
| `qrcode[pil]` | QR code generation |
| `uvicorn[standard]` | ASGI server that runs FastAPI |

Development dependencies: `httpx`, `pytest`, `pytest-asyncio`, `ruff`.

**ASGI** stands for Asynchronous Server Gateway Interface. It is the standard interface between
Python async web applications and servers. Uvicorn is an ASGI server; FastAPI is an ASGI
application.

### Type hints and dataclasses

The code uses Python type hints (`str | None`, `list[Device]`) and dataclasses (`Config`,
`PrintRequest`, `Device`, `JobRecord`). These make the code easier to read and catch mistakes with
static tools.

A **dataclass** is a class that automatically generates `__init__`, `__repr__`, and equality methods
from its fields. It is a compact way to define a record.

## 5.3 Architecture and layers

```text
┌──────────────────────────────────────────────────────────────┐
│ Front doors: CLI, web UI, OpenCode plugin, curl              │
├──────────────────────────────────────────────────────────────┤
│ HTTP service: FastAPI, JSON, static files, history endpoints │
├──────────────────────────────────────────────────────────────┤
│ PrinterManager: lifecycle, lock, retries, auto-detection     │
├──────────────────────────────────────────────────────────────┤
│ Transports: MXW01 BLE, Luck BLE, serial ESC/POS, mock        │
├──────────────────────────────────────────────────────────────┤
│ Protocol builders: frames, raster, status, ESC/POS           │
├──────────────────────────────────────────────────────────────┤
│ Renderer: markdown-lite → fonts → layout → 1-bit image       │
└──────────────────────────────────────────────────────────────┘
```

The dependencies point downward. The renderer does not know about BLE. The protocol layer does not
know about HTTP. The transport does not know about the web UI. The manager coordinates them.

### Why this separation matters

- **Testability:** render and protocol can be tested without hardware.
- **Replaceability:** swap MXW01 for LuckPrinter or mock without touching the UI.
- **Debuggability:** isolate whether a failure is rendering, protocol, transport, or service.
- **Reuse:** the same manager serves CLI, web, and agent.

**Analogy:** a restaurant. The recipe (renderer) is separate from the language the kitchen uses
(protocol), the waiter (transport), the head waiter (manager), and the front desk (HTTP service).
You can change the front desk without rewriting the recipes.

### Code tour

| File | Responsibility |
| --- | --- |
| `src/thermal/protocol.py` | Pure protocol helpers: MXW01 framing/CRC/raster/status, LuckPrinter commands, ESC/POS raster |
| `src/thermal/transports/base.py` | `Device`, `PrinterTransport`, `TransportError`, `ProtocolMismatch` |
| `src/thermal/transports/luck.py` | BLE discovery/connection, LuckPrinter/ESC-POS, scan, chunking, notifications |
| `src/thermal/transports/mxw01.py` | MXW01 GATT resolution, notification parser, status/battery/info, print flow |
| `src/thermal/transports/mock.py` | Fake printer; saves Portable Network Graphics (PNG) files for UI/dev |
| `src/thermal/transports/serial.py` | ESC/POS over serial |
| `src/thermal/render.py` | Markdown-lite parser, fonts, canvas, five styles, QR, image prep, 1-bit threshold |
| `src/thermal/printer.py` | Manager: lifecycle, protocol auto-retry, history, print request |
| `src/thermal/service.py` | FastAPI app + static web UI |
| `src/thermal/cli.py` | `thermal ...` commands |
| `src/thermal/config.py` | Config file + environment overrides |
| `src/thermal/history.py` | Job records, PNG previews, pruning |
| `src/thermal/web/` | No-build Hypertext Markup Language (HTML)/Cascading Style Sheets (CSS)/JavaScript (JS) UI |
| `.opencode/plugins/thermal/index.ts` | OpenCode tools + daemon auto-start |
| `.opencode/skills/thermal-printer/SKILL.md` | Agent formatting/safety guidance |
| `tests/` | 33 tests: protocol, render, transports, API, web IDs |

## 5.4 The transport interface

The `PrinterTransport` protocol defines the contract:

```python
class PrinterTransport(Protocol):
    name: str

    @property
    def connected(self) -> bool: ...
    @property
    def label(self) -> str: ...

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def print_image(self, image, *, density=None, feed_dots=80, label_mode=False) -> dict: ...
    async def status(self) -> Any: ...
    async def battery(self) -> int | None: ...
    async def info(self) -> dict: ...
    async def diagnostics(self) -> dict: ...
```

A **Protocol** in Python is structural typing: any class with these methods can be used, without
explicit inheritance. This keeps the manager decoupled from concrete transports.

### Implementations

- `Mxw01BleTransport`: MXW01-specific GATT resolution and command flow.
- `LuckBleTransport`: LuckPrinter/DP-L1S and ESC/POS over BLE.
- `SerialEscPosTransport`: ESC/POS over a serial port.
- `MockTransport`: a fake printer that saves PNGs and returns synthetic status.

The mock transport is important: it lets the web UI, CLI, and tests run without hardware. It is a
**flight simulator** for the printer.

### ProtocolMismatch

`ProtocolMismatch` extends `TransportError` and carries a `suggested` protocol. The manager catches
it in `auto` mode, switches, and retries. This is how the driver discovers that an “MXW01” name is
actually a LuckPrinter, or vice versa.

## 5.5 Config and persistence

### Config file

`~/.config/thermal-printer/config.json` holds:

```json
{
  "transport": "ble",
  "protocol": "mxw01",
  "device_name": "MXW01",
  "width_px": 384,
  "density": 1,
  "feed_dots": 80,
  "chunk_size": 512,
  "chunk_delay_ms": 10,
  "api_host": "127.0.0.1",
  "api_port": 8787,
  "auto_connect": true
}
```

The `Config` dataclass loads the file, applies environment overrides, and validates values through
the CLI/API. `Config.save()` writes it back atomically enough for a local tool (it writes the whole
file).

### Precedence

1. Defaults in the dataclass.
2. Config file values.
3. Environment variables.
4. Explicit CLI flags or API payload fields (for that operation).

This is a common pattern: defaults < file < environment < command line.

### Environment variables

Useful overrides:

| Variable | Meaning |
| --- | --- |
| `THERMAL_DEVICE` | BLE device name (substring match) |
| `THERMAL_ADDRESS` | BLE address/UUID |
| `THERMAL_TRANSPORT` | `auto`, `ble`, `serial`, or `mock` |
| `THERMAL_PROTOCOL` | `auto`, `mxw01`, `luck`, or `escpos` |
| `THERMAL_WIDTH` | Print width in pixels (default 384) |
| `THERMAL_DENSITY` | 0–2 |
| `THERMAL_FEED` | Feed dots after a print |
| `THERMAL_API_PORT` | Web/API port (default 8787) |
| `THERMAL_MOCK=1` | Use the mock printer |

### Why config matters for UX

Without persisted config, every command would need the device name, protocol, width, density, and
feed. With it, `thermal print "hello"` just works. The trade-off is that stale config can cause
confusing failures; the CLI and UI expose `config show` and diagnostics to make it inspectable.

## 5.6 PrinterManager: the head waiter

`PrinterManager` owns the lifecycle:

- **One lock** (`asyncio.Lock`) serializes connect/disconnect/print operations.
- **Transport creation** based on config.
- **Auto-connect** before printing if disconnected.
- **Protocol mismatch retry** in `auto` mode.
- **Status cache** for 30 seconds to avoid hammering the printer.
- **Info cache** for model/firmware.
- **Print throttle** of 0.8 seconds between jobs.
- **Copies** limited to 1–5.
- **History** saved on success and failure.
- **Last error** for the UI.

### Why a lock?

BLE operations are stateful and not safe to interleave. If two HTTP requests print at the same time,
the command frames and image rows could interleave, producing garbage. The lock serializes the
critical sections.

**Analogy:** a single bathroom key. Only one person can use the bathroom at a time; everyone else
waits in line. The key prevents two people from walking in at once.

### Status caching

`status()` returns a cached result if it is less than 30 seconds old and `refresh` is false. This
makes the UI responsive without querying the printer on every poll. A print invalidates the cache.

### Failure previews

If a print fails, the manager saves the rendered PNG as a dry-run history record and raises a
`TransportError` with the preview path. This is important UX: the user can see what was attempted,
even if the printer never received it.

### Copies

`copies` is clamped to 1–5. Each copy calls `print_image` again, with the 0.8-second throttle
between jobs. There is no persistent queue; copies run in-process.

## 5.7 HTTP and the FastAPI service

### What HTTP is

**HTTP** (Hypertext Transfer Protocol) is a request/response protocol:

```text
Client → request:  method, Uniform Resource Locator (URL), headers, optional body
Server → response: status code, headers, optional body
```

Common methods:

- **GET:** retrieve something.
- **POST:** create or perform an action.
- **PATCH:** partially update something.
- **PUT:** replace something.
- **DELETE:** remove something.

Common status codes:

- **200 OK:** success.
- **201 Created:** created.
- **400 Bad Request:** malformed request.
- **404 Not Found:** no such resource.
- **413 Payload Too Large:** body too large.
- **422 Unprocessable Entity:** validation failed.
- **500 Internal Server Error:** server bug.
- **503 Service Unavailable:** dependency unavailable (the printer, in this case).

**Analogy:** a written order slip. The method is “place order” or “check order”; the URL is the
counter; the headers are instructions; the body is the order; the status code is the clerk’s
response.

### JSON

**JSON** (JavaScript Object Notation) is a text format for structured data:

```json
{
  "title": "BUILD COMPLETE",
  "message": "The build is **green**.",
  "style": "note",
  "dry_run": false
}
```

It is easy for humans to read and easy for programs to parse. It is not the only option (Extensible
Markup Language (XML), YAML Ain't Markup Language (YAML), and Protocol Buffers exist), but it is the
default for local web APIs.

### REST

**REST** (Representational State Transfer) is a style of designing HTTP APIs around resources and
methods. This project is REST-ish:

- `/api/status` (GET) returns state.
- `/api/print` (POST) performs an action.
- `/api/history` (GET) returns a list.
- `/api/jobs/{id}/preview.png` (GET) returns an image.

It is not a strict REST implementation, and that is fine for a local tool.

### FastAPI

FastAPI turns Python functions into HTTP endpoints and uses **Pydantic** models to validate request
bodies. For example:

```python
class PrintPayload(BaseModel):
    message: str = Field(default="", max_length=8000)
    title: str | None = Field(default=None, max_length=200)
    style: Literal["note", "receipt", "quote", "sticker", "task"] = "note"
    copies: int = Field(default=1, ge=1, le=5)
    dry_run: bool = False
```

If a client sends an invalid style or too many copies, FastAPI returns a 422 with details. This
validation is a form of defensive programming: the printer should never receive malformed input.

### Uvicorn

**Uvicorn** is the ASGI server that runs the FastAPI app. It listens on a host and port, accepts
HTTP connections, and passes requests to the app.

```sh
uv run thermal serve
```

runs Uvicorn in the foreground. The process stays alive until you stop it.

### Static files

The web UI is served by `StaticFiles` from `src/thermal/web/`. There is no build step, no Node
toolchain, and no bundler. The same process serves the API and the UI.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness/version |
| `GET` | `/api/status` | Connection, model, firmware, battery, state |
| `GET` | `/api/devices` | Scan for BLE devices |
| `POST` | `/api/scan` | Scan with timeout |
| `POST` | `/api/connect` | Connect by name/address/protocol |
| `POST` | `/api/disconnect` | Drop BLE connection |
| `GET` | `/api/config` | Current config |
| `PATCH` | `/api/config` | Update config |
| `POST` | `/api/preview` | Render PNG preview |
| `POST` | `/api/print` | Render + print (or dry-run) |
| `GET` | `/api/history` | Recent jobs |
| `GET` | `/api/jobs/{id}/preview.png` | Job preview PNG |
| `GET` | `/api/diagnostics` | GATT dump + device info |
| `GET` | `/api/templates` | Style metadata + samples |

### Error handling

`TransportError` is mapped to HTTP 503 with `{"detail": ..., "kind": "printer"}`. Validation errors
are 422. A missing job preview is 404. An oversized image is 413. This lets the UI distinguish “your
input is wrong” from “the printer is unavailable.”

### CORS

**CORS** (Cross-Origin Resource Sharing) is a browser security mechanism. The API allows the local
UI origins (`127.0.0.1:8787`, `localhost:8787`) so the browser can call the API. It does not open
the API to the public internet.

### Lifespan and auto-connect

FastAPI’s lifespan hook starts a background task that tries to connect to the printer up to three
times, 1.5 seconds apart, if `auto_connect` is enabled and a device name/address is configured. It
cancels the task and closes the manager on shutdown.

**Analogy:** the shop opens, the clerk checks whether the phone line is working, and retries a few
times before putting up a “printer offline” sign.

## 5.8 The web UI: no build step

The UI is three files:

- `src/thermal/web/index.html` — structure,
- `src/thermal/web/style.css` — visual design,
- `src/thermal/web/app.js` — behavior.

### Why no framework?

- No Node/npm install for the user.
- No bundler or build step.
- Easy to serve as static files.
- Small enough to read in one sitting.

The trade-off is manual Document Object Model (DOM) manipulation and less structure for a large
app. For a local tool with one screen, it is the right trade.

### Preview flow

1. The user types.
2. `schedulePreview()` debounces for 260 ms.
3. `refreshPreview()` sends the same payload as a print to `POST /api/preview`.
4. The response is a PNG blob.
5. The UI creates an object URL and sets it as the image source.
6. The old object URL is revoked to avoid memory leaks.
7. The response headers `X-Print-Width` and `X-Print-Height` update the size label.

**Debounce** means “wait until the user stops typing for 260 ms, then act.” It prevents a request
per keystroke.

**Object URL** is a browser URL that points to an in-memory blob (`blob:...`). It must be revoked
when no longer needed.

### Status and connection

- `refreshStatus()` calls `/api/status`.
- The status pill shows `offline`, `online`, `paper empty`, `cover open`, `overheating`, or `mock`.
- The battery percentage is displayed.
- The printer panel shows model, firmware, and transport.
- **Scan Bluetooth** calls `/api/devices?timeout=8`, lists devices, and marks likely printers.
- **Connect** posts the selected device address/name to `/api/connect`.
- **Use mock printer** connects the mock transport.
- **Disconnect** posts to `/api/disconnect`.

### Print and dry-run

- **Print** posts `dry_run: false`.
- **Save preview only** posts `dry_run: true`.
- The message box reports transport and elapsed time.
- History refreshes after each job.

### History

`/api/history?limit=24` returns job records. Each record has a preview URL, title, style, dimensions,
timestamp, source, and dry-run/printed flag. The UI renders thumbnails and links to the full PNG.

### Accessibility and UX details

- The message box uses `aria-live="polite"` so screen readers announce status changes.
- Buttons are disabled while a job is busy.
- Error messages are plain language.
- The preview is shown on a “paper” background.
- The app is local-only and does not require an account.

## 5.9 History and storage

Each job saves:

- a PNG preview,
- a JSON record with id, timestamp, title, style, width, height, bytes, duration, transport,
  source, dry_run, printed, and preview URL.

The store keeps the newest 200 jobs and deletes older PNG/JSON pairs. This prevents unbounded disk
growth.

**Analogy:** a receipt drawer. You keep recent receipts for reference, but you do not keep every
receipt forever.

## 5.10 Tests and tooling

### pytest

**pytest** is a Python test runner. The project uses:

- `pytest-asyncio` for async tests,
- FastAPI’s `TestClient` for API tests,
- Pillow images for render tests,
- byte vectors for protocol tests,
- mock transports for manager tests.

### Known-answer tests

Protocol tests compare exact bytes:

```python
assert protocol.mxw01_frame(0xA1, b"\x00") == bytes.fromhex("22 21 A1 00 01 00 00 00 FF")
assert protocol.mxw01_crc8(b"123456789") == 0xF4
```

These are **known-answer tests** (also called golden vectors). They catch regressions in framing,
CRC, bit order, and status parsing.

### Bit-order tests

The raster tests cover:

- leftmost black,
- rightmost black,
- all black,
- all white,
- padding to width.

These are the tests that would have caught an inverted or reversed image immediately.

### API tests

`TestClient` sends real HTTP requests to the FastAPI app in-process. The tests cover health,
preview, mock print, and response headers.

### Web tests

The web tests parse `index.html` and `app.js` and assert that every ID used by JavaScript exists in
the HTML, and that IDs are unique. This catches a common class of UI breakage without a browser.

### Linting and syntax checks

- `uv run ruff check .` checks Python style and common bugs.
- `node --check src/thermal/web/app.js` checks JavaScript syntax.

Current status: **33 tests passing**, ruff clean, JS syntax clean.

### What is not tested

Hardware behavior is manual:

- actual BLE connection,
- GATT discovery on the real device,
- print quality,
- paper/cover/battery errors,
- timing.

The mock transport and byte vectors reduce risk, but the first physical print is still the real
integration test.

## 5.11 Security and privacy

- The API binds to `127.0.0.1` by default. It is not exposed to the network.
- There is no authentication. Do not expose it publicly without adding auth.
- Printed text is stored as PNG previews and JSON metadata under
  `~/.local/share/thermal-printer/history/`.
- Do not print secrets, tokens, passwords, or private user data.
- The OpenCode plugin can require approval before printing.
- The agent skill explicitly warns against printing sensitive data.

**Analogy:** a local print shop with no front-door lock because it is inside your house. That is
fine until you move it to the street.

---

# Part 6 — The user experience: CLI, web UI, and OpenCode plugin

## 6.1 Three front doors, one printer

The same core serves three audiences:

- **CLI:** fast, scriptable, terminal-native.
- **Web UI:** visual, preview-first, good for composing and connecting.
- **OpenCode plugin:** the agent can print as a tool call, with no manual copy/paste.

All three go through the same `PrinterManager` and the same protocol code. That consistency is the
point: a fix in the renderer improves every front door.

## 6.2 The CLI

### Command overview

| Command | Purpose |
| --- | --- |
| `thermal scan` | Scan for nearby BLE devices; mark likely printers |
| `thermal doctor` | Connect, dump GATT, query model/battery/status |
| `thermal connect` | Connect and save the selected device/protocol |
| `thermal status` | Show connection, battery, state, errors |
| `thermal print` | Render and print a message |
| `thermal preview` | Render a PNG without printing |
| `thermal usb-probe` | List USB/serial devices |
| `thermal config` | Show/path/set configuration |
| `thermal serve` | Run the local web UI and API |

### Quick start

```sh
uv sync
uv run thermal scan
uv run thermal connect --name MXW01 --protocol mxw01
uv run thermal print "Hello from my laptop" --title "HELLO"
uv run thermal serve --open
```

### `thermal print` behavior

1. Build a `PrintRequest` from CLI arguments.
2. If `--no-daemon` is not set, check `GET /api/health` with a 0.6 s timeout.
3. If healthy, `POST /api/print` with a 180 s timeout.
4. If not healthy, connect directly and print in-process.
5. Print a human-readable result or JSON with `--json`.

Why the daemon path? It reuses an existing BLE connection and avoids a second process fighting for
the printer. Why the direct path? It works even if the daemon is not running.

**Analogy:** calling the restaurant (daemon) versus cooking at home (direct). The restaurant is
convenient and shares the kitchen; cooking at home works when the restaurant is closed.

### Examples

```sh
# A simple note
uv run thermal print "The build is green." --title "BUILD COMPLETE"

# Markdown-lite task list
uv run thermal print --title "TODAY" --style task \
  "[x] review the diff
[ ] fix the flaky test
[ ] take a walk"

# Receipt with right-aligned amounts
uv run thermal print --style receipt --title "AGENT RECEIPT" \
  "feature work        1
tests               12
bugs introduced      0"

# Big sticker
uv run thermal print --style sticker "SHIP
IT"

# QR code + image
uv run thermal print "Scan for the deploy logs" --qr "https://example.com/logs"
uv run thermal print "Latest screenshot" --image ./screenshot.png

# Preview only
uv run thermal print "Hello" --dry-run
uv run thermal preview "Hello" --out preview.png
```

### stdin and piping

If no text argument is given and stdin is not a terminal, the CLI reads stdin. This allows:

```sh
git log -1 --pretty=%B | uv run thermal print --title "LAST COMMIT"
```

### Exit codes

- `0`: success.
- `1`: printer/transport error or no connection.
- `2`: usage/config error.
- `130`: interrupted (Ctrl-C).

Exit codes matter for scripts and CI.

### `thermal status`

It tries the daemon first (`/api/status?refresh=true`, 25 s timeout) and falls back to a direct
connection. The JSON includes:

- `connected`,
- `transport`,
- `label`,
- `status` (state, battery, temperature, error),
- `battery`,
- `info` (model, firmware),
- `last_error`.

### `thermal doctor`

`doctor` connects directly, dumps the GATT services/characteristics, queries model/firmware/serial,
status, and battery, and prints the result. `--json` gives machine-readable output. This is the
first tool to run when a new printer is not behaving.

**Analogy:** a doctor’s check-up. It does not fix the patient; it tells you what is connected, what
it reports, and where the problem might be.

## 6.3 The web UI

### First run

1. Start the server:

   ```sh
   uv run thermal serve --open
   ```

2. The browser opens `http://127.0.0.1:8787`.
3. Wake the printer and force-quit **Fun Print**.
4. Click **Scan Bluetooth**.
5. Pick the row marked `* MXW01`.
6. Wait for the status pill to say **online**.
7. Compose a message and click **Print**.

### Compose panel

- **Title:** optional heading.
- **Message:** markdown-lite body.
- **Style:** note, receipt, quote, task, sticker.
- **QR / URL:** optional QR code.
- **Footer:** optional small line; empty string hides the automatic timestamp.
- **Image:** optional image file.
- **Density:** 0 light, 1 normal, 2 dark.
- **Feed:** 0–200 dots in the UI (the backend accepts 0–255).
- **Label mode:** die-cut label positioning for compatible media.
- **Print / Refresh preview / Save preview only.**

### Preview panel

- Live 384 px preview.
- Shows dimensions and “1-bit.”
- Download PNG link.
- Inspiration samples.

### Printer panel

- Status pill and battery.
- Transport label.
- Model and firmware.
- **Scan Bluetooth**, **Use mock printer**, **Disconnect**.
- Device list with “likely” badges.

### History

- Recent prints with thumbnails.
- Style, dimensions, printed/preview, source, timestamp.
- Click a thumbnail to open the full PNG.

### Reconnect flow

If the printer sleeps or the phone app takes over:

1. Press the printer’s power/feed button to wake it.
2. Force-quit **Fun Print**; turn off phone Bluetooth if it keeps reconnecting.
3. In the web UI, click **Connect** and pick `* MXW01`.
4. Or simply print again; the daemon reconnects before printing.
5. If the daemon is stuck, restart it:

   ```sh
   pkill -f "thermal serve" || true
   uv run thermal serve --open
   ```

6. Confirm with `uv run thermal status`.

### Why the UI shows physical state

The printer is not a stateless web service. Paper empty, cover open, low battery, overheating, sleep,
and phone-app ownership are all real states. The UI surfaces them instead of showing a generic
“error.”

**Analogy:** a car dashboard. “Check engine” is less useful than “low oil” or “door open.” Good
device software names the physical problem.

## 6.4 The OpenCode plugin

### What OpenCode is

OpenCode is a coding agent environment. It can load plugins that register tools the agent can call.
A tool is a function with a name, description, input schema, and execute function.

The thermal plugin lives at `.opencode/plugins/thermal/index.ts` and is auto-discovered when the
project is opened in OpenCode.

### What it registers

A `thermal` namespace with two tools:

- **`print`** — render and print a note.
- **`status`** — check connection, battery, paper/cover, and model.

The exact display name depends on the client; the README calls them `print` and `status`.

### Print tool input

| Field | Type | Meaning |
| --- | --- | --- |
| `message` | string | Body, markdown-lite, keep short |
| `title` | string? | Optional heading |
| `style` | enum | note, receipt, quote, sticker, task |
| `footer` | string? | Optional footer; empty hides timestamp |
| `qr` | string? | URL/text to encode |
| `image_path` | string? | Absolute path to an image |
| `copies` | 1–5 | Number of copies |
| `dry_run` | boolean | Preview only |
| `invert_image` | boolean | Invert the image |
| `feed_dots` | 0–255 | Extra feed |
| `density` | 0–2 | Print density |
| `label_mode` | boolean | Die-cut label positioning |

### Auto-start flow

1. The tool calls `ensureDaemon()`.
2. It checks `GET /api/health` with a 900 ms timeout.
3. If healthy, it reuses the daemon.
4. If not, and `autoStart` is enabled, it spawns:

   ```sh
   uv run --project <projectDir> thermal serve --host 127.0.0.1 --port 8787
   ```

   with `detached: true`, `stdio` redirected to `.thermal/daemon.log`, and `unref()` so the plugin
   does not wait for it.
5. It polls health up to 50 times, 300 ms apart (15 seconds).
6. If healthy, the tool proceeds; otherwise it returns a startup error with the log path.

**Analogy:** a concierge who checks whether the kitchen is open. If not, they open it in the back,
wait for the lights to come on, and then place your order.

### Print flow

1. Optionally assert permission (`requireApproval`).
2. Ensure the daemon.
3. If `image_path` is set, read the file, enforce a 12 MB limit, and base64-encode it.
4. Build a JSON body with `source: opencode:<project>`.
5. `POST /api/print`.
6. Return a short message with dimensions, transport, duration, and preview URL.

### Status flow

1. Ensure the daemon.
2. `GET /api/status?refresh=true`.
3. Return the JSON as text.

### Why `source` matters

The history records where a job came from: `cli`, `web`, or `opencode:<project>`. This helps debug
“who printed this?” and makes demos more informative.

### Approval mode

If `requireApproval` is true, the plugin calls `ctx.permission.assert` with the action
`thermal.print` and the service URL. This lets the user approve or deny agent-initiated prints.

**Analogy:** a note on the office printer that says “please approve before printing.” Useful when
the agent might otherwise produce a lot of paper.

### Companion skill

`.opencode/skills/thermal-printer/SKILL.md` tells the agent:

- When to print (deliberate action, not after every task).
- Keep it short (~30 lines).
- Use high-contrast text; no photos unless asked.
- Prefer dry-run when unsure.
- Do not print secrets/tokens/private data.
- Wake the printer; force-quit Fun Print.

The skill is part of the UX: it shapes the agent’s behavior before the tool is even called.

### Failure modes the agent should explain

- Service not running and auto-start failed.
- Printer not connected / asleep.
- Paper empty / cover open / low battery.
- Image too large.
- Print rejected by the printer.
- Timeout waiting for `AA`.

The tool returns plain text errors so the agent can relay them to the user.

## 6.5 `thermal serve` versus `thermal serve --open`

This is a common point of confusion.

### `uv run thermal serve`

- Starts the FastAPI/Uvicorn server.
- Binds to `127.0.0.1:8787` by default.
- **Blocks in the foreground.** The terminal stays attached to the server.
- Serves the API and the web UI.
- Auto-connects to the printer if configured.

### `uv run thermal serve --open`

- Does exactly the same thing.
- Additionally schedules `webbrowser.open(url)` after 0.8 seconds.
- **Does not daemonize.** It does not put the server in the background.
- If the server is already running, the new process will try to bind port 8787 and fail.

**Analogy:** `serve` opens the restaurant. `--open` also puts a sign outside and invites you in.
Neither one hires a night manager to keep the restaurant running after you leave.

### What the plugin does

The plugin starts `thermal serve` **detached**, without `--open`, and redirects logs to
`.thermal/daemon.log`. It health-checks first and reuses an existing server.

### What `thermal print` does

It uses the daemon if healthy; otherwise it connects directly. `--no-daemon` forces direct.

### Useful commands

```sh
# Start the server + open the browser
uv run thermal serve --open

# Start the server only (good for a terminal tab / plugin)
uv run thermal serve

# If it is already running
open http://127.0.0.1:8787

# Stop it
pkill -f "thermal serve"

# Check the port
lsof -ti tcp:8787
```

### Port conflicts

If port 8787 is already in use, either:

- open the existing UI, or
- stop the old process, or
- start on another port:

  ```sh
  uv run thermal serve --port 8788
  ```

The plugin and CLI use the configured URL/port; if you change it, update the plugin options or
`THERMAL_URL`.

## 6.6 UX principles

1. **Show physical state.** Battery, paper, cover, temperature, sleep, and connection ownership are
   part of the interface.
2. **Make errors actionable.** “No BLE device matching 'MXW01'. Wake the printer, force-quit the
   phone app, and retry” is better than “connection failed.”
3. **Offer dry-run.** Printing is slow and physical; preview first.
4. **Keep agent output short.** A receipt is a high-signal artifact, not a document.
5. **Respect the medium.** 384 px, 1-bit, thermal paper. Design for it.
6. **Be local-first.** No cloud, no account, no vendor app.
7. **Protect privacy.** Do not print secrets; history is stored locally.
8. **Make the daemon optional.** The CLI works without it; the plugin starts it lazily.
9. **Serialize access.** One printer, one connection, one lock.
10. **Explain the weird parts.** `--open` does not daemonize; `AA` can be missing; the advertised
    service is not the command service.

## 6.7 Three end-to-end flows

### CLI flow

```text
User types command
  → argparse parses flags
  → PrintRequest built
  → daemon health check
  → POST /api/print (or direct manager)
  → render() → 1-bit image
  → protocol.mxw01_raster() → rows
  → Mxw01BleTransport.print_image()
  → BLE writes
  → AA or timeout
  → history saved
  → terminal prints dimensions, transport, duration, preview path
```

### Web flow

```text
User edits form
  → 260 ms debounce
  → POST /api/preview
  → PNG blob → object URL → preview
User clicks Print
  → POST /api/print
  → same render/protocol/transport path
  → history refresh
  → status refresh
  → message box shows result
```

### Agent flow

```text
User: "Print a receipt of what you changed."
  → agent calls thermal.print
  → plugin ensures daemon (health check / spawn)
  → POST /api/print with source=opencode:<project>
  → render/protocol/transport
  → tool returns "Printed 384x... in ... ms. Preview: ..."
  → agent summarizes to user
```

---

# Part 7 — The bug log: what broke, why, and what it taught

This is the most useful section for a blog post. Each bug is a small story with a symptom, a
hypothesis, evidence, a fix, and a general lesson. The analogies are there to make the technical
details memorable.

## 7.1 Wrong protocol family assumption

**Symptom:** The generic BLE transport connected, but the device did not behave like a
LuckPrinter/DP-L1S. Commands were ignored or produced no useful output.

**Cause:** Earlier research targeted the LuckPrinter/DP-L1S family. The MXW01 is a different
protocol with a different GATT layout and a different command framing.

**Fix:**

- Added `Mxw01BleTransport`.
- Added `ProtocolMismatch(message, suggested)`.
- In `protocol = "auto"` mode, the manager catches the mismatch, recreates the transport with the
  suggested protocol, and retries once.

**Lesson:** Device names are hints; GATT layout is the fingerprint.

**Analogy:** using a French phrasebook in Spain. Some words look similar, but the conversation
never quite works. The fix is to recognize the language from the environment, not the label on the
book.

## 7.2 `af30` advertisement versus `ae30` GATT

**Symptom:** The scan showed `af30`, while protocol notes said `ae30`; it was unclear which service
to use.

**Cause:** BLE advertisements can list service UUIDs that are not the command service. The GATT
table after connection is the source of truth.

**Fix:** Connect, enumerate services/characteristics, and use `ae30` with `ae01`/`ae02`/`ae03`.

**Lesson:** Never build a driver from advertisement data alone.

**Analogy:** a shop sign that says “Service entrance” but points to a locked door. The building
directory inside tells you where the actual counter is.

## 7.3 CRC variant confusion

**Symptom:** The test expected `0xA1` for `"123456789"`, but the implementation returned `0xF4`.
Frames with a wrong CRC may be silently ignored.

**Cause:** “CRC-8” is ambiguous. `0xA1` is the check value for the reflected Dallas/Maxim variant.
This protocol uses non-reflected CRC-8, polynomial `0x07`, init `0x00`, which gives `0xF4`.

**Fix:** Corrected the docstring and test vector; kept the implementation; verified on hardware.

```python
def mxw01_crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc
```

**Lesson:** Specify polynomial, init, refin/refout, and xorout — not just “CRC-8.”

**Analogy:** two recipes both called “bread.” One uses yeast, one uses baking soda. The name is the
same; the result is not. You need the full recipe.

## 7.4 Image bit order and polarity

**Symptom:** Pixels came out inverted, shifted, or generally wrong.

**Cause:** Pillow’s 1-bit mode packs **0 = black, MSB first**. The MXW01 expects **1 = black, LSB
first**. Also, `image.convert("L")` is not the packed row bytes.

**Fix:** Use the packed bytes, invert each byte, and reverse the bit order with a lookup table:

```python
raw = image.tobytes()
return bytes(_REVERSE_BITS[255 - value] for value in raw)
```

Tests cover leftmost black, rightmost black, all black, and all white.

**Lesson:** Raster formats differ in bit order and polarity. Write tests with known pixels.

**Analogy:** reading a book left-to-right when the printer reads right-to-left, and treating black
ink as white paper. Two separate conventions, both wrong at once.

## 7.5 Text not aligned: the baseline bug

**Symptom:** Words without ascenders (`aaa`) sat higher than words with ascenders (`haa`). Mixed
bold/italic/code runs looked jittery.

**Cause:** Pillow’s `anchor="la"` anchors each text run to its own glyph bounding box top. Top
alignment is not baseline alignment. A word like `aaa` has no ascenders, so its box top is lower
than `haa`; drawing both at the same `y` makes them sit at different baselines.

**Fix:** Compute a shared baseline from the font metrics and draw every run with `anchor="ls"`:

```python
baseline = canvas.y + max(_ascent(run.font) for run in line)
for run in line:
    canvas.text(cursor, run.text, run.font, anchor="ls", baseline=baseline)
    cursor += _measure(canvas.draw, run.text, run.font)
```

`_ascent(font)` uses `font.getmetrics()` so the ascent is font-constant, not glyph-dependent.

**Regression test:** `test_words_share_a_baseline` finds the ink columns for two words, groups
them, and asserts their bottom rows are equal.

**Lesson:** Typography bugs that look like “the font is wrong” are often anchor/coordinate bugs.

**Analogy:** aligning the tops of letters instead of the feet. A tall letter and a short letter
start at the same height, so the short letter’s feet float above the line.

## 7.6 Receipt columns not lining up

**Symptom:** Amounts in receipt lines did not line up on the right edge.

**Cause:** Padding with spaces assumes fixed-width glyphs and the same font size. Proportional
fonts and mixed runs break character-count alignment.

**Fix:** Split on two or more spaces, measure the right column in pixels, and draw it at the right
edge:

```python
match = re.match(r"^(.*?)\s{2,}(\S.*)$", line)
if match and _measure(canvas.draw, line, font) <= canvas.content_width:
    left, right = match.group(1), match.group(2)
    canvas.text(canvas.margin, left, font)
    rw = _measure(canvas.draw, right, font)
    canvas.text(canvas.right - int(rw), right, font)
```

If the line is too wide, fall back to normal wrapping.

**Lesson:** Measure pixels, not characters.

**Analogy:** using a ruler to place the amount at the right edge, instead of counting spaces and
hoping the letters are the same width.

## 7.7 Cold-start auto-connect flakiness

**Symptom:** The first status call after starting the daemon reported disconnected; one run hit
`'NoneType' object has no attribute 'lower'` in the BLE stack.

**Cause:** BLE stack not ready / transient CoreBluetooth failure on a cold start.

**Fix:** Auto-connect retries three times with a 1.5 s delay; only logs a warning after the final
failure. The user can also retry from the UI.

**Lesson:** BLE startup is flaky; retries belong in the driver, not in the user’s workflow.

**Analogy:** waking up a sleeping shopkeeper. The first knock may happen before they are awake; a
few retries are normal.

## 7.8 Printer sleeps / phone app takes the connection

**Symptom:** A CLI print worked, then a web print failed with
`No BLE device matching 'MXW01'`.

**Cause:** The printer had gone to sleep and stopped advertising, or the phone app had reclaimed
the single BLE connection. Not a web UI bug.

**Fix:** Better error message (“wake the printer, force-quit Fun Print”), docs, and a reconnect
flow. The daemon auto-connects on startup and before printing.

**Lesson:** BLE peripherals are stateful and single-owner. UX must explain physical state.

**Analogy:** a single-occupancy phone booth. If the phone app is inside, the laptop waits outside.
No amount of clicking “Connect” will change that until the occupant leaves.

## 7.9 Missing acks

**Symptom:** Sometimes no `A9` ack or no `AA` completion notification, even though the print
physically happened.

**Cause:** Notifications can be dropped; firmware revisions differ.

**Fix:** Tolerate a missing `A1`/`A9` ack; still wait for `AA` but report `acked: false`; do not
treat a missing `AA` as a failed print if the output is correct.

**Lesson:** Separate transport success from physical-completion evidence.

**Analogy:** the receipt printed, but the “order up” bell did not ring. The food is still on the
counter.

## 7.10 Paper feed

**Symptom:** Paper not advanced enough; content cut off at the tear bar.

**Cause:** `A3` eject feed distance is less predictable; image height and feed were treated
separately.

**Fix:** Append blank rows to the image data and include them in the `A9` height. `feed_dots`
controls how many blank rows.

**Lesson:** Feed is part of the raster contract, not an afterthought.

**Analogy:** the printer does not understand “leave a margin.” It understands “print 208 rows, and
the last 80 are blank.”

## 7.11 Debugging methodology

When a new BLE device misbehaves, use this order:

1. **Check physical state.** Power, paper, cover, battery, sleep, phone app.
2. **Scan.** Confirm the device is advertising; note the name, address/UUID, RSSI, and advertised
   services.
3. **Connect and dump GATT.** Use `thermal doctor --json` or a generic BLE explorer. Record every
   service, characteristic, property, and handle.
4. **Identify the write/notify/data characteristics.** Look for write-without-response and notify.
   Do not assume the advertised service is the command service.
5. **Subscribe to notifications.** Confirm the CCCD write succeeds.
6. **Send a known query.** For MXW01, `A1 00` is a good first command. Capture the raw notification
   bytes.
7. **Find the framing.** Look for magic bytes, length fields, command bytes, checksums, and
   terminators.
8. **Test one variable at a time.** Change one byte, one bit order, or one timing parameter.
9. **Use known-answer tests.** Encode expected frames and pixel rows before touching hardware.
10. **Preview before printing.** A PNG preview catches layout bugs without wasting paper.
11. **Compare with independent implementations.** If three projects agree on a command sequence,
    that is stronger evidence than one blog post.
12. **Document the exact firmware and model.** Behavior varies.

### Layer isolation

Ask: which layer failed?

- **Render:** Does the preview look right?
- **Protocol:** Do the bytes match the known frames?
- **Transport:** Is the device connected? Are notifications arriving?
- **Service:** Is the HTTP request valid? Is the daemon healthy?
- **Physical:** Is there paper? Is the cover closed? Is the battery charged?

**Analogy:** a doctor does not prescribe medicine before deciding whether the problem is the heart,
the lungs, or a sprained ankle. Isolate the system first.

---

# Part 8 — Reuse versus new work

## 8.1 The short version

- **Reused:** protocol knowledge, command constants, framing, status offsets, raster conventions,
  and the general BLE reverse-engineering approach.
- **New:** the Python implementation, async transport architecture, protocol auto-detection,
  renderer, CLI, FastAPI daemon, web UI, history/config, tests, docs, and OpenCode plugin.
- **No vendor software development kit (SDK) or app code was copied.** No upstream code is vendored
  in this repo. If you plan to copy code (rather than protocol facts), check each project’s license.

## 8.2 GitHub projects used as references

| Project | Language/approach | What it contributed |
| --- | --- | --- |
| [PinThePenguinOne/MXW01_Thermal-Printer-Tool](https://github.com/PinThePenguinOne/MXW01_Thermal-Printer-Tool) | Python + Bleak CLI | Proof that a Python BLE path works; image/text printing, feed, 1-bit conversion; protocol cross-check |
| [MaikelChan/CatPrinterBLE](https://github.com/MaikelChan/CatPrinterBLE) | .NET 8, Windows | Detailed MXW01 reverse engineering; status fields, 1bpp/4bpp modes, dithering, command flow; the `A1` offset layout was verified against it |
| [clementvp/mxw01-thermal-printer](https://github.com/clementvp/mxw01-thermal-printer) | TypeScript, framework-agnostic | Independent cross-check of the print sequence (`setIntensity`, `requestStatus`, `printRequest`, `sendDataChunks`, `flushData`, `waitForPrintComplete`) |
| [ChiaraCannolee/thermal-pocket-printer-basic](https://github.com/ChiaraCannolee/thermal-pocket-printer-basic) | Python + web app | LuckPrinter/DP-L1S protocol and fallback implementation; decompiled Android Package Kit (APK) notes; hardware verification |
| [rbaron/catprinter](https://github.com/rbaron/catprinter) + [BLE reverse-engineering write-up](https://rbaron.net/blog/2021/08/15/Reverse-engineering-the-cat-printer-bluetooth-protocol) | Python + BLE | Background on the broader cat-printer family and BLE reverse-engineering methodology |
| [0xMH/fichero-printer](https://github.com/0xMH/fichero-printer) | Python | Related LuckPrinter-family label printer |

### What each project actually gave us

- **PinThePenguinOne:** a working Python/Bleak path and a sanity check that the MXW01 can be driven
  from a laptop. It confirmed the general shape of the print flow.
- **MaikelChan:** the most detailed protocol notes. The `A1` status offsets and the 1bpp/4bpp
  distinction were cross-checked here.
- **clementvp:** an independent TypeScript implementation. The fact that three implementations
  converge on the same sequence gave confidence before the first hardware test.
- **ChiaraCannolee:** the LuckPrinter/DP-L1S fallback, including the preamble and ESC/POS raster
  path, plus the reminder that the app is often the only documentation.
- **rbaron:** the broader cat-printer family and a clear reverse-engineering narrative. Useful for
  explaining the methodology, not for MXW01-specific bytes.
- **0xMH:** related hardware and command patterns for the LuckPrinter family.

### Why cross-checking mattered

Three independent implementations (Python/Bleak, .NET, TypeScript) agree on:

- the `22 21` frame header,
- the command IDs,
- the `A9` print request shape,
- the 48-byte rows,
- the `AA` completion event,
- the 1-bit raster conventions.

That convergence is evidence. The first physical print (“MXW01 ONLINE”) confirmed the framing, CRC,
bit order, and feed.

## 8.3 What is genuinely new here

- **Unified driver:** MXW01 + LuckPrinter + ESC/POS + mock behind one `PrinterTransport` interface.
- **Auto-detection:** connect once, inspect GATT, raise `ProtocolMismatch` with a suggested
  protocol, recreate the transport, retry.
- **Renderer:** markdown-lite → five print styles → 1-bit bitmap, with baseline and receipt-column
  fixes.
- **Local daemon + web UI:** no build step, local-only, live preview, history, scan/connect.
- **Agent integration:** OpenCode tools + skill, lazy daemon start, dry-run, approval option.
- **Tests:** protocol byte vectors, bit order, status parsing, transport selection, auto-retry,
  API, UI IDs.
- **Docs:** `README.md` quick start, `PROTOCOL.md` full reference, troubleshooting.

## 8.4 What was learned versus copied

| Fact | Source | How used |
| --- | --- | --- |
| MXW01 frame header / command IDs | public reverse-engineering projects | Reimplemented in Python |
| CRC-8 poly/init | public projects + hardware check | Reimplemented; test vector `0xF4` |
| `A1` status offsets | CatPrinterBLE | Reimplemented; verified on hardware |
| `A9` payload / 48-byte rows | public projects | Reimplemented; verified |
| 1-bit LSB-first packing | public projects | Reimplemented with lookup table + tests |
| LuckPrinter command set | ChiaraCannolee | Reimplemented fallback |
| ESC/POS `GS v 0` | standard | Standard raster path |
| Baseline / receipt layout | original | New renderer code |

## 8.5 Licensing and credit

Protocol facts are not the same as source code. Reimplementing a command sequence from
documentation is different from copying a file. Still:

- Read each project’s license before copying code.
- Credit the projects that supplied protocol knowledge.
- Do not imply endorsement.
- Keep your own implementation and tests so you can fix and extend it.

**Blog angle:** “Standing on the shoulders of reverse engineers” is accurate and generous. Name the
projects and say exactly what each one contributed.

---

# Part 9 — What this says about printer tech in general

## 9.1 The app is the driver

These printers are not self-describing. The vendor app contains the protocol knowledge, the
rendering rules, and the device quirks. When the app dies or demands an account, the hardware
becomes e-waste unless someone reverse-engineers the GATT layer.

**Analogy:** a lock with no key and no locksmith. The hardware still works; the knowledge to use it
is trapped in a binary.

**Implication:** documentation and open drivers are part of repairability. A cheap printer without
a documented protocol is a temporary product, not a durable tool.

## 9.2 Advertisement is not the API

The advertised service can be a red herring. The real contract is the GATT table after connection:
services, characteristics, properties, and the byte protocol on top.

**Analogy:** a restaurant’s roadside sign versus its actual menu. The sign tells you it exists; the
menu tells you what you can order.

**Implication:** generic BLE tools should always enumerate GATT, not trust the advertisement.

## 9.3 Binary protocols are simple but unforgiving

A frame header, length, payload, CRC, and terminator is easy to describe. But one wrong bit order,
CRC variant, or polarity produces garbage. Tests with known byte vectors are essential.

**Analogy:** a combination lock. The instructions are simple, but one wrong number means it does
not open. “Almost right” is still wrong.

**Implication:** protocol work needs known-answer tests, hex dumps, and a willingness to compare
implementations.

## 9.4 Throughput is not just MTU

The MXW01 has a 185-byte MTU but expects 48-byte rows with delays. The printer’s buffer and
physical print head pace the job. “Send faster” is not always better.

**Analogy:** a wide highway with a slow toll booth. The lanes are wide, but the booth sets the
pace.

**Implication:** measure the whole pipeline, not just the radio’s theoretical maximum.

## 9.5 Rendering is the real product

The radio is commodity. The difference between a useful printout and a smudge is the renderer:
font choice, layout, thresholding, dithering, margins, and feed.

**Analogy:** two photographers with the same camera. The camera is not the photograph; the
composition and processing are.

**Implication:** invest in typography, preview, and paper handling. They are what users see.

## 9.6 Local daemon as device broker

Single-connection peripherals need an owner. A local daemon lets CLI, web, and agent share the
device without fighting over it. This pattern generalizes to scanners, cameras, serial gadgets, and
lab equipment.

**Analogy:** a receptionist who owns the only phone line. Everyone leaves a message; the
receptionist serializes the calls.

**Implication:** if a device is single-connection or stateful, put a broker in front of it. The
broker can cache state, retry, and expose a stable API.

## 9.7 Agent output changes the design

An agent-written note is not a document. It is a short, high-signal artifact: a status, a receipt,
a checklist, a QR handoff. The tool should encourage brevity, structure, and dry-run.

**Analogy:** a sticky note versus a report. The sticky note is more useful when it is short and
placed where you need it.

**Implication:** design tool schemas and skills for short outputs, not for dumping a whole chat
transcript onto paper.

## 9.8 Physical state is part of the API

Paper, cover, battery, temperature, sleep, and phone-app ownership are all state. A good driver
surfaces them instead of pretending the printer is a stateless socket.

**Analogy:** a car dashboard. You need to know fuel, temperature, and doors, not just “engine on.”

**Implication:** status endpoints should expose physical state in human terms, and errors should
tell the user what to do.

## 9.9 Local-first is a feature

No cloud, no account, no vendor app. The API binds to `127.0.0.1`; printed previews stay on the
machine. For a device that prints private notes, that matters.

**Analogy:** a home printer versus a print shop that requires an account and uploads your documents.
Convenience is not worth losing control of the data.

**Implication:** local-first design is a differentiator, not a limitation.

## 9.10 Repairability and longevity

Reverse-engineered drivers keep old hardware useful. The same approach can rescue receipt
printers, label makers, scanners, and other abandoned peripherals.

**Analogy:** repairing a lamp instead of throwing it away because the switch is proprietary.

**Implication:** document the protocol, publish the driver, and keep the hardware out of landfill.

## 9.11 Protocol families are rebadged

The same physical printer can appear as `MXW01`, `DP-L1S`, `C&Co 3128_BLE`, `MX06`, etc. Detect by
GATT layout, not by marketing name.

**Analogy:** the same car sold under different badges. The engine is the same; the logo is not the
specification.

**Implication:** build protocol detection and fallbacks. Do not hard-code one name.

## 9.12 The 1-bit constraint is clarifying

No color, no grayscale (in mono mode), 384 px wide. You must decide what matters. That constraint
makes the output feel like a receipt, not a screenshot.

**Analogy:** a haiku. The form is limited, so every word has to earn its place.

**Implication:** constraint improves agent output. A receipt forces prioritization.

## 9.13 Security, privacy, and e-waste

- A local API with no auth is fine on localhost, dangerous on a network.
- Printed history is data at rest; treat it like logs.
- Vendor apps may require accounts and cloud access; local drivers avoid that.
- Cheap hardware without open protocols becomes e-waste when the app disappears.

**Blog angle:** the environmental and privacy angles are real, but keep them grounded in the
specific device and workflow.

---

# Part 10 — Ideas and future work

## 10.1 Agent-native printing ideas

- **Task receipt:** what changed, tests run, files touched, next steps.
- **Build/test result:** “BUILD GREEN” + commit + duration; QR to CI logs.
- **pull request (PR) review card:** diff stats, risk level, checklist, reviewer.
- **Incident card:** severity, service, timeline, QR to dashboard/runbook.
- **Daily standup / journal:** yesterday/today/blockers.
- **Physical kanban:** one card per task; move it on the desk.
- **Deploy receipt:** version, environment, timestamp, rollback command.
- **Agent handoff:** summary for the next session or human.
- **QR handoff:** print a QR to a local dashboard, logs, or a PR.
- **Label printing:** cables, boxes, server racks, prototypes.
- **CI webhook:** print on red build or failed deploy.
- **Scheduled summaries:** cron a daily “what happened” receipt.
- **MCP server:** expose the same API to other agents/clients, not just OpenCode.
- **Home Assistant / email-to-print:** local automation triggers.
- **Art/fun:** generative receipts, physical changelogs, party invites, “ticket” stubs.

## 10.2 Technical next steps

- **4bpp grayscale mode:** use the documented `0x02` mode for photos.
- **Persistent queue:** survive daemon restarts and retry failed jobs.
- **Authentication:** if the API is ever exposed beyond localhost.
- **Multi-printer support:** choose a printer per job or per profile.
- **Better paper/cover handling:** surface sensor states in the UI and block unsafe prints.
- **Templates:** saved layouts for common agent outputs.
- **MCP server:** make the bridge available to more agent runtimes.
- **CI integration:** GitHub Actions or a local webhook.
- **Scheduling:** daily standup receipts.
- **Label mode:** die-cut labels and gap calibration.
- **Image pipeline:** better face/photo handling, contrast presets, crop controls.
- **Accessibility:** larger type, high-contrast preview, screen-reader labels.

## 10.3 Guardrails

- Printing is slow and physical; do not spam.
- Dry-run first for layout-sensitive jobs.
- Approval mode for agent-initiated prints.
- Never print secrets or personal data without explicit intent.
- Keep messages short; a receipt is not a document.
- Explain physical failures in plain language.
- Respect the printer’s single connection and sleep behavior.

## 10.4 What I would build next

1. **A proper queue** with job states and retries.
2. **An MCP server** so any agent can print, not just OpenCode.
3. **A CI hook** that prints a build receipt on failure.
4. **Better templates** for PR reviews, incidents, and standups.
5. **4bpp photo mode** for richer images.
6. **A “print preview” approval step** in the plugin by default.

---

# Appendix A — Glossary of every acronym and term

| Term | Expansion | Plain-English meaning |
| --- | --- | --- |
| **ACK** | Acknowledgement | A message that says “I received it.” |
| **AI** | Artificial Intelligence | Software that performs tasks associated with human reasoning. |
| **API** | Application Programming Interface | A defined way for one program to call another. |
| **APK** | Android Package Kit | The installable package format for Android apps. |
| **ASCII** | American Standard Code for Information Interchange | A 7-bit character encoding; `A` = 65. |
| **ASGI** | Asynchronous Server Gateway Interface | Python’s standard interface between async web apps and servers. |
| **ATT** | Attribute Protocol | The BLE protocol for reading/writing attributes; GATT is built on it. |
| **BLE** | Bluetooth Low Energy | The low-power Bluetooth variant used by the printer. |
| **BPP** | Bits Per Pixel | How many bits encode one pixel; 1 bpp means black or white. |
| **BR/EDR** | Basic Rate / Enhanced Data Rate | Bluetooth Classic, the higher-throughput branch. |
| **CCCD** | Client Characteristic Configuration Descriptor | The per-characteristic switch that enables notifications/indications. |
| **CI** | Continuous Integration | Automated build/test/deploy pipelines. |
| **CLI** | Command-Line Interface | A text interface in a terminal. |
| **CORS** | Cross-Origin Resource Sharing | A browser rule for which web origins may call an API. |
| **CPU** | Central Processing Unit | The main processor that executes instructions. |
| **CRC** | Cyclic Redundancy Check | A checksum used to detect corrupted data. |
| **CSS** | Cascading Style Sheets | The language for styling web pages. |
| **DLE** | Data Link Escape | The control byte `0x10`, used in ESC/POS and LuckPrinter commands. |
| **DMA** | Direct Memory Access | Hardware that moves data to/from memory without the CPU copying each byte. |
| **DOM** | Document Object Model | The browser’s tree representation of an HTML page. |
| **DPI** | Dots Per Inch | Print resolution; 203 dpi here. |
| **ESC/POS** | Epson Standard Code for Point of Sale | A family of receipt-printer command sequences. |
| **EXIF** | Exchangeable Image File Format | Metadata embedded in image files, including orientation. |
| **FF** | `0xFF` (hex byte) | The byte value 255; also the MXW01 frame terminator. Not the ASCII form-feed control code (`0x0C`). |
| **GAP** | Generic Access Profile | The BLE profile for discovery, advertising, and connection basics. |
| **GATT** | Generic Attribute Profile | The BLE database of services and characteristics. |
| **GFSK** | Gaussian Frequency-Shift Keying | The modulation used by BLE. |
| **GND** | Ground | The shared voltage reference for a circuit. |
| **GS** | Group Separator | The control byte `0x1D`, used in ESC/POS commands. |
| **HCI** | Host Controller Interface | The boundary/protocol between the BLE host and controller. |
| **HTML** | Hypertext Markup Language | The structure language of web pages. |
| **HTTP** | Hypertext Transfer Protocol | The request/response protocol of the web. |
| **HTTPS** | HTTP Secure | HTTP over Transport Layer Security (TLS) encryption. |
| **I²C** | Inter-Integrated Circuit | A two-wire synchronous bus for chips. |
| **IEEE** | Institute of Electrical and Electronics Engineers | The professional body that maintains many networking standards. |
| **IP** | Internet Protocol | The network-layer protocol that addresses and routes packets. |
| **ISM** | Industrial, Scientific, and Medical | The unlicensed radio bands, including 2.4 GHz. |
| **JS** | JavaScript | The programming language of web browsers. |
| **JSON** | JavaScript Object Notation | A text format for structured data. |
| **L2CAP** | Logical Link Control and Adaptation Protocol | The BLE layer that carries ATT and other protocols. |
| **LAN** | Local Area Network | A network within a small area. |
| **LE** | Low Energy | The “LE” in BLE. |
| **LE16** | Little-Endian 16-bit | A two-byte integer with the low byte first. |
| **LSB** | Least Significant Bit | The bit with value 1 in a byte; first in LSB-first packing. |
| **MAC** | Media Access Control | A 48-bit hardware address; not the same as a macOS CoreBluetooth UUID. |
| **MCP** | Model Context Protocol | A protocol for exposing tools/resources to AI agents. |
| **MITM** | Man-in-the-Middle | An attacker between two parties. |
| **MSB** | Most Significant Bit | The bit with value 128 in a byte; first in MSB-first packing. |
| **MTU** | Maximum Transmission Unit | The largest packet/payload a link can carry. |
| **OS** | Operating System | macOS, Linux, Windows, etc. |
| **OSI** | Open Systems Interconnection | A seven-layer networking reference model. |
| **PCIe** | Peripheral Component Interconnect Express | A high-speed serial bus for internal peripherals. |
| **PNG** | Portable Network Graphics | A lossless image format; used for previews. |
| **PR** | Pull Request | A code-change proposal in a version-control system. |
| **QR** | Quick Response | A two-dimensional barcode. |
| **RAM** | Random Access Memory | Working memory. |
| **REST** | Representational State Transfer | A style of HTTP API design around resources. |
| **RGB** | Red, Green, Blue | A color model; the printer is 1-bit, so color is reduced. |
| **RSSI** | Received Signal Strength Indicator | A radio signal strength measurement in dBm. |
| **SDK** | Software Development Kit | A vendor’s library/tools for a platform. |
| **SIG** | Special Interest Group | The Bluetooth SIG maintains the Bluetooth specifications. |
| **SMBus** | System Management Bus | The bus family whose CRC-8 variant matches this protocol’s CRC. |
| **SMP** | Security Manager Protocol | The BLE protocol for pairing and key distribution. |
| **SPI** | Serial Peripheral Interface | A synchronous four-wire bus for chips. |
| **TCP** | Transmission Control Protocol | A reliable, ordered transport protocol. |
| **TLS** | Transport Layer Security | The encryption layer under HTTPS. |
| **TTL** | Transistor-Transistor Logic | A logic-level family; also “time to live” in networking. |
| **UART** | Universal Asynchronous Receiver/Transmitter | A common asynchronous serial interface. |
| **UDP** | User Datagram Protocol | An unreliable, connectionless transport protocol. |
| **UI** | User Interface | The part of the software a human interacts with. |
| **URL** | Uniform Resource Locator | A web address. |
| **USB** | Universal Serial Bus | A host-centric serial bus for peripherals. |
| **UTF-8** | Unicode Transformation Format, 8-bit | A variable-length encoding of Unicode. |
| **UUID** | Universally Unique Identifier | A 128-bit identifier for GATT services/characteristics. |
| **UX** | User Experience | The overall feel and usability of the product. |
| **Wi-Fi** | Wireless Fidelity | A family of wireless LAN standards; shares 2.4 GHz with BLE. |
| **XML** | Extensible Markup Language | A text format for structured documents. |
| **XOR** | Exclusive OR | A bitwise operation that outputs 1 when exactly one input bit is 1. |
| **YAML** | YAML Ain't Markup Language | A human-readable text format for structured data. |

Other useful terms:

- **Daemon:** a long-running background process. Here, the local HTTP/BLE bridge.
- **Driver:** software that knows how to talk to a device.
- **Firmware:** software inside the device.
- **Frame:** a structured message with a header, payload, and trailer.
- **Packet:** a small unit of data sent over a network/link.
- **Endianness:** the order of bytes in a multi-byte number.
- **Baseline:** the line on which text sits.
- **Dithering:** approximating shades with patterns of black/white dots.
- **Thresholding:** converting grayscale to black/white at a cutoff.
- **Monospace:** a font where every character has the same width.
- **Proportional:** a font where characters have different widths.
- **dBm:** decibels relative to one milliwatt; the unit used for radio power and RSSI.
- **Hz:** hertz, one cycle per second.
- **GHz:** gigahertz, one billion cycles per second.
- **px:** pixels; one pixel is one dot in the rendered image.

# Appendix B — Byte-level cheat sheet

## MXW01 frame

```text
22 21 <cmd> 00 <len LE16> <payload> <crc8> FF
```

## Common frames

```text
Status:     22 21 A1 00 01 00 00 00 FF
Firmware:   22 21 B1 00 01 00 00 00 FF
Battery:    22 21 AB 00 01 00 00 00 FF
Intensity:  22 21 A2 00 01 00 5D 94 FF
Print 208:  22 21 A9 00 04 00 D0 00 30 00 34 FF
Flush:      22 21 AD 00 01 00 00 00 FF
```

## Status payload

```text
offset 0: state (0 standby, 1 printing, 2 feeding, 3 ejecting)
offset 3: battery %
offset 4: temperature °C
offset 6: status (0 = OK)
offset 7: error code (01/09 no paper, 04 overheated, 08 low battery)
```

## Raster

```text
width:      384 px
row:        48 bytes
bit order:  LSB first
polarity:   1 = black
feed:       append blank rows; include in A9 height
```

## Timing

```text
row write:  48 bytes
delay:      10 ms
208 rows:   9,984 bytes, 208 writes, 2.08 s of delays
timeout:    max(15, height / 20) seconds
```

# Appendix C — Layer map

| Layer | OSI-ish name | BLE equivalent | This project |
| --- | --- | --- | --- |
| 1 | Physical | 2.4 GHz radio, GFSK | Printer radio, antenna, battery |
| 2 | Data link | BLE link layer, advertising, connections | Bleak/OS controller |
| 3 | Network | None (BLE is local) | — |
| 4 | Transport | ATT (and L2CAP below it) | GATT writes/notifications |
| 5 | Session | GATT connection | Bleak client |
| 6 | Presentation | Byte encoding, CRC, raster | MXW01 frames, 1-bit packing |
| 7 | Application | GATT profile / vendor protocol | Print command, status, renderer |
| 8 | Service | HTTP/JSON | FastAPI daemon |
| 9 | UX | CLI/web/plugin | `thermal` commands, UI, agent tools |

# Appendix D — Demo material and numbers

## Scan

```text
Scanning BLE for 8s...
* MXW01   6D717764-D3E3-2239-C52D-42759E25147F   -55 dBm
    services: 0000af30-0000-1000-8000-00805f9b34fb
```

## GATT

- Service `ae30`
- Control `ae01` (write-without-response)
- Notify `ae02`
- Data `ae03` (write-without-response)
- Secondary `ae04`/`ae05`/`ae10`
- Secondary service `ae3a` with `ae3b`/`ae3c`

## Device info

- Firmware `1.9.3.1.1`
- Battery 89–90%
- Temperature 20–21 °C
- State `standby`, `ok`

## Prints

- `MXW01 ONLINE`: 384×218 px, 10.1 s, direct CLI.
- `WEB PATH OK`: 384×128 px render, 208 rows with feed, 9,984 bytes, 7.9 s, `acked: true`,
  daemon/web API.

## Tests/tooling

- 33 tests passing.
- `ruff check .` clean.
- `node --check src/thermal/web/app.js` clean.
- Web UI HTTP 200 at `http://127.0.0.1:8787`.

## Assets to capture for the post

- Photo of the printer with a printed note.
- Terminal screenshot: `thermal scan` / `thermal status`.
- GATT dump from `thermal doctor --json`.
- Web UI screenshot with preview and history.
- OpenCode tool call + printed result.
- Code snippets: frame, CRC, bit packing, baseline fix.
- Architecture diagram.
- A before/after of the baseline bug if possible.

# Appendix E — Suggested blog outline

1. **Hook:** the printer only works with a phone app; I wanted my coding agent to print.
2. **Hardware:** MXW01, 384 px, 1-bit, BLE, Fun Print.
3. **Data transfer primer:** bits, bytes, frames, checksums, layers (keep it short in the post).
4. **BLE primer:** advertising, connections, GATT, notifications, MTU.
5. **The red herring:** `af30` advertisement versus `ae30` GATT.
6. **The protocol:** framing, CRC, commands, status, raster.
7. **The build:** renderer, daemon, web UI, CLI.
8. **The bugs:** wrong protocol, CRC variant, bit order, baseline, receipt columns.
9. **The agent:** OpenCode plugin, dry-run, approval, ideas.
10. **Lessons:** app-as-driver, local daemon, rendering, physical state, local-first.
11. **Future:** MCP, CI hooks, templates, 4bpp, multi-printer.

## What to emphasize

- The first physical print is the payoff; show the note.
- The `af30`/`ae30` discovery is a good “trust but verify” story.
- The baseline bug is relatable and visual; show before/after if possible.
- The 48-byte rows + 10 ms delay explain why it takes ~8–10 s.
- The local daemon is the architectural insight, not just plumbing.
- The agent plugin is the “why bother” answer.

## What to leave out (or put in an appendix)

- Full GATT dump (link to `PROTOCOL.md` instead).
- Every command in the protocol table.
- Full code listings; use short snippets.
- Setup instructions; link to `README.md`.
- Too much BLE spec detail; enough to understand the story.
- The complete glossary; link to it or include a short “terms” box.

# Appendix F — Source links

- [PinThePenguinOne/MXW01_Thermal-Printer-Tool](https://github.com/PinThePenguinOne/MXW01_Thermal-Printer-Tool)
- [MaikelChan/CatPrinterBLE](https://github.com/MaikelChan/CatPrinterBLE)
- [clementvp/mxw01-thermal-printer](https://github.com/clementvp/mxw01-thermal-printer)
- [ChiaraCannolee/thermal-pocket-printer-basic](https://github.com/ChiaraCannolee/thermal-pocket-printer-basic)
- [rbaron/catprinter](https://github.com/rbaron/catprinter)
- [Reverse-engineering the cat printer Bluetooth protocol](https://rbaron.net/blog/2021/08/15/Reverse-engineering-the-cat-printer-bluetooth-protocol)
- [0xMH/fichero-printer](https://github.com/0xMH/fichero-printer)
- [Bleak](https://github.com/hbldh/bleak)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Pillow](https://python-pillow.org/)
- [qrcode](https://github.com/lincolnloop/python-qrcode)

# Appendix G — Open questions and caveats

- USB-C data capability unconfirmed; likely charging-only.
- Tested on macOS + one MXW01 firmware (`1.9.3.1.1`).
- Linux/BlueZ path is implemented but not hardware-tested here.
- 4bpp grayscale mode (`0x02`) is documented but not implemented.
- Some commands (`A3` eject, `A4` retract, `A7` count, `AC` cancel, `B0` print type) are
  documented but not exposed in the CLI/UI.
- `AA` completion notifications can be dropped; `acked: false` is not always a failure.
- No auth on the local API; it binds to `127.0.0.1`. Do not expose it publicly.
- No persistent print queue; jobs run in-process.
- Single printer at a time.
- Hardware tests are manual; unit tests use mock transports and byte vectors.
- macOS Bluetooth permissions can reset; grant them to the terminal/OpenCode.
- The printer accepts one central at a time; force-quit the phone app.
- The CoreBluetooth UUID is machine-specific; use the name `MXW01`.

# Appendix H — Project docs to link

- `README.md` — quick start, CLI, web UI, plugin, troubleshooting.
- `PROTOCOL.md` — full MXW01 + LuckPrinter protocol reference.
- `src/thermal/protocol.py` — framing, CRC, raster helpers.
- `src/thermal/transports/mxw01.py` — BLE transport and print flow.
- `src/thermal/render.py` — renderer and baseline fix.
- `.opencode/plugins/thermal/index.ts` — OpenCode tools.
- `.opencode/skills/thermal-printer/SKILL.md` — agent guidance.

---

## Final note for the blog

The strongest narrative is not “I built a printer driver.” It is:

> A cheap printer is a small computer with a radio and a secret language. The app is the only
> translator. Once you learn the language, you can put the printer behind a local API, a web UI, and
> an agent tool — and suddenly a device that only worked with a phone becomes part of your
> workflow.

Use the layers in this document as the spine of the post. Start with the physical constraint
(384 dots, thermal paper), climb through BLE and GATT, decode the protocol, show the rendering
bugs, and end with the agent printing a receipt. The reader should finish understanding not just
*what* was built, but *why every layer exists*.



