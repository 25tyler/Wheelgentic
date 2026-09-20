What we already have:

- camera (Intel RealSense D455 — RGB works on macOS, depth does not; both work on the GB10)  
- Wheelchair  
- Sponges  
- Power extension  
- Power bar  
- USB hub  
- USB type c to type c  
- 1x robotic arm (Anvil OpenYAM, 6 joints, Damiao servos, CAN bus)  
- CANable 2.0 CAN adapter (gs_usb firmware, needs a Linux host; still on the Mac)  
- Linux host (NVIDIA GB10, Ubuntu 24.04, aarch64, `ssh wg`) — the Pi is not what
  arrived. Depth works on it. CAN has not been run on it: the adapter is not
  plugged in yet, and bringing `can0` up needs a sudo password.

&nbsp;

What we need to request:

- 3x more robotic arms (the vision is 4; we have 1)  
- 24V supply for the OpenYAM servos  
- 120Ω CAN termination resistors  
- power for the GB10 host at the bench (its supply came with it; nobody has
  checked whether the bench has a free outlet for it)  
- Jumper wires  
- Breadboard  
- AGX Spark \+ Asus Display  
- Arduino uno R3 Q (UNO R3 Project Most Complete Starter Kit)  
- Expressif esp32  
- Esp&nbsp;  
- Joystick \+ four buttons  
- LCD screen  
- MAX30102 pulse oximetry and heart-rate sensors, and photoplethysmography pulse sensors  
- Raspberry pi touchscreen  
- Hdmi (micro/mini hdmi)  
- usb c  
- usb a

&nbsp;

Robot arms

Zip ties

Usb c

Usb a

Nordic board&nbsp;

&nbsp;

Notes on the arm and camera: `docs/OPENYAM-BRINGUP.md` is the box-to-first-motion
procedure and lists the five values that must be measured before the arm moves.
The D455 gives RGB only on macOS; depth works on the GB10 Linux host.
`docs/STATE.md` §3 says what is measured and what is not.
