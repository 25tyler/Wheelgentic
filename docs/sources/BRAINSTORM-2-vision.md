- hardware foundation (Justin & Crystal)&nbsp;  
- Software vision/shower (lJustin and Tyler)  
- Vitals & arduino & software display (Crystal & Justin)  
- Voice control (Artem & tyler)  
- &nbsp;

&nbsp;

&nbsp;

&nbsp;

&nbsp;

We are building an “AI wheelchair” that will have multiple robotic arms (4) that will be able to do multiple tasks, such as, Shower, Eating/drinking, Feeding Pills, Vitals (Any health sensing, Put it on the chair), Voice support / personal agent (Claude computer use, Voice control).&nbsp;

&nbsp;

1. Physically impressive  
2. UI

&nbsp;

Magnet to change tools?

- Manual change tools  
- magnets??/

&nbsp;

3. Shower  
4. Eating/drinking  
   1. &nbsp;Feeding Pills  
5. Vitals  
   1. Any health sensing  
   2. Put it on the chair  
6. Voice support / personal agent  
   1. Claude computer use?  
   2. Voice control

&nbsp;

Hardware

- 4x Roarm m2s  
- Intel Realsense d455  
- Power bar&nbsp;  
- Power station  
- Power extension cord  
- USB hub  
- Double sided tape  
- sponges

&nbsp;

&nbsp;

https://plume.hackmit.org/gallery?hackathon\_id=hack-2025\&page=1

&nbsp;

Agentic Multi-Arm Robotic AI Bathing Assistant / Shower for the Disabled and Elderly

&nbsp;

Background

- Bathing is the \#1 daily activity people lose first and need the most help with  
- 74.5% of US residential care residents need help bathing  
- Bathing disability is the main reason older people need a home aide and strongly predicts nursing home admission.  
- 63M US family caregivers  
- Nursing assistants get injured at 5x the industry rate. Mostly from the back/shoulder from handling people.  
- Dignity: being undressed and scrubbed by someone else feels like being vulnerable or attached

&nbsp;

&nbsp;

- 4x Waveshare high-torque robotic arm. Each arm would act as an independent AI agent.&nbsp;  
- Spinning sponge on the tip of each arm?  
  - Big wow factor, but each one needs a servo \+ microcontroller \+ wiring \+ power  
  - Might not be enough time  
- Vision  
  - Use point cloud only to map out 3D version of human body instead of video to address the privacy concerns.  
  - If we ignore the privacy concern and use cameras we can identify the most dirty areas to clean  
    - We can maybe say all data is processed locally?  
  - Use the 3d version of the human body to plan out the trajectories of each robotic arm  
    - split the body into 4 sections? one arm handles each section of the body  
  - “Innovation” part to tell the judges?  
    - No preprogrammed actions. Every body type is different so paths are generated from the person’s actual 3D model  
    - Arms adapt dynamically to movement. If the person moves their leg, the arm adjusts to the new position  
  - User can indicate an area to clean more, and the arm focuses there  
- User sits on the chair and presses start  
- Body Visualization \+ completion status (e.g 50% complete) on laptop or monitor or phone?  
- Demo  
  - Let judges experience it?  
    - Not like actually touching the body but just like hovering above the body?  
  - Artem in swimsuit and we do live demo  
  - No water, will damage the motors  
- Restriction  
  - Rules say bench mounted only. This is vague though. How to circumvent it?  
  - Arm uses clamping mechanism, could we just clamp on the chair?

&nbsp;

We are doing not shower but also eating, vitals, and other shit.&nbsp;

&nbsp;

&nbsp;