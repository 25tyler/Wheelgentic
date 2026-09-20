import { SerialPort } from 'serialport';
import { createSerialHardware, identifyBoard } from './hardware-serial.js';

const ports = await SerialPort.list();
console.log('Connected serial devices:');
for (const port of ports) console.log(`${port.path}: ${identifyBoard(port) || port.friendlyName || 'Unknown device'}`);
if (!process.argv.includes('--watch')) process.exit(0);
const index = process.argv.indexOf('--port');
const hardware = createSerialHardware({path:index >= 0 ? process.argv[index + 1] : process.env.ARDUINO_PORT});
hardware.events.on('snapshot', value => console.log(JSON.stringify(value)));
hardware.events.on('button', value => console.log(`BUTTON ${value.button} PRESSED`));
hardware.start();
const keepAlive = setInterval(()=>{},60000);
process.on('SIGINT', async()=>{clearInterval(keepAlive);await hardware.stop();process.exit(0);});
