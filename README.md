# Wheelgentic frontend

Light-blue, responsive assistive wheelchair care dashboard. Frontend only; no robotic arm, sensor, dispensing, AI, or email backend is connected.

## Run
Requires Node.js 18 or later. No package installation is needed.

```sh
npm run dev
```

Open http://127.0.0.1:5173. `npm run check` checks JavaScript syntax.

## Included
- Meal, 250 ml water, comfort, and simulated bathing logs
- Sample morning/evening medication tracker with confirmation and duplicate prevention
- Mood check-ins and timestamped activity journal
- Local browser persistence, reset on a new local calendar day
- Explicitly simulated vitals and a scripted text companion
- Downloadable text care report and caretaker email draft (user sends in email app)
- Responsive navigation, accessible dialogs, keyboard controls

Data stays in localStorage on this browser. Clearing site data removes it. No authentication or server database is implemented. Google Fonts is optional; system fonts provide a fallback. The four-arm wheelchair illustration is inline SVG.

## Files
- index.html: application shell and wheelchair illustration
- style.css: light-blue design and responsive layouts
- app.js: client state, navigation, dialogs, tracking, and reports
- server.js: local development server

Replace the isolated UI actions with authenticated backend integrations before connecting any hardware. The frontend currently never sends motion or medication commands.
