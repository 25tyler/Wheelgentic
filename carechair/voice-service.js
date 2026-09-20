import { AppError } from './voice-config.js';
import { validateInput, directControl, controlCommand, validateCommand } from './voice-commands.js';
export function createVoiceService(providers, robot) {
  let generation = 0;
  let busy = false;
  async function dispatch(command) {
    if (['stop', 'pause'].includes(command.action)) generation++;
    if (command.action === 'none') return { status: 'not_sent' };
    return robot.dispatch(command);
  }
  function responseFor(command, delivery) {
    if (delivery.status === 'cancelled') return 'Your request was cancelled.';
    if (command.action === 'none') return command.response;
    if (['stop', 'pause'].includes(command.action)) return delivery.mode === 'demo'
      ? `Your ${command.action} request is ready.` : `I've sent your ${command.action} request.`;
    const areas = { left_arm: 'your left arm', right_arm: 'your right arm', both_arms: 'both arms', body: 'your body' };
    const task = command.category === 'showering' ? `washing ${areas[command.target]}${command.action === 'repeat' ? ' again' : ''}`
      : command.category === 'eating' ? (command.item === 'water' ? 'drinking water' : 'eating') : 'taking your prescribed medication';
    return delivery.mode === 'demo' ? `Okay, your request for help ${task} is ready.`
      : `Okay, I've sent your request for help ${task}.`;
  }
  return {
    async process(body) {
      const { transcript, history } = validateInput(body);
      const control = directControl(transcript);
      if (control) {
        const delivery = await dispatch(control);
        return { transcript, command: control, understanding: `You want to ${control.action} the current task.`, suggestion: 'none', response: responseFor(control, delivery), delivery, compression: 'skipped' };
      }
      if (busy) throw new AppError('Please wait for your current request to finish.', 409, 'BUSY');
      busy = true;
      const startedAt = generation;
      try {
        const context = await providers.compress(history);
        const interpretation = await providers.interpret(transcript, context.history);
        const command = validateCommand(interpretation.command);
        const delivery = startedAt === generation ? await dispatch(command) : { status: 'cancelled' };
        return { transcript, command, understanding: interpretation.understanding,
          suggestion: delivery.status === 'cancelled' ? 'none' : interpretation.suggestion,
          response: responseFor(command, delivery), delivery, compression: context.status };
      } finally { busy = false; }
    },
    async control(action) {
      const command = controlCommand(action);
      const delivery = await dispatch(command);
      return { command, delivery, understanding: `You want to ${action} the current task.`, suggestion: 'none', response: responseFor(command, delivery) };
    },
    async task(command) { return dispatch(validateCommand(command)); },
  };
}
