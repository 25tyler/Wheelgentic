import { AppError } from './voice-config.js';
import { validateInput, directControl, controlCommand, validateCommand } from './voice-commands.js';
export function createVoiceService(providers, robot) {
  let generation = 0;
  let busy = false;
  async function dispatch(command) {
    if (['stop', 'pause'].includes(command.action)) generation++;
    return robot.dispatch(command);
  }
  function responseFor(command, delivery) {
    if (delivery.status === 'cancelled') return 'Your request was cancelled.';
    if (command.action === 'none') return command.response;
    const label = ['stop', 'pause'].includes(command.action) ? command.action : command.category.replaceAll('_', ' ');
    return delivery.mode === 'demo' ? `Your ${label} request is ready.`
      : `Your ${label} request has been sent.`;
  }
  return {
    async process(body) {
      const { transcript, history } = validateInput(body);
      const control = directControl(transcript);
      if (control) {
        const delivery = await dispatch(control);
        return { transcript, command: control, response: responseFor(control, delivery), delivery, compression: 'skipped' };
      }
      if (busy) throw new AppError('Please wait for your current request to finish.', 409, 'BUSY');
      busy = true;
      const startedAt = generation;
      try {
        const context = await providers.compress(history);
        const command = validateCommand(await providers.interpret(transcript, context.history));
        const delivery = startedAt === generation ? await dispatch(command) : { status: 'cancelled' };
        return { transcript, command, response: responseFor(command, delivery), delivery, compression: context.status };
      } finally { busy = false; }
    },
    async control(action) {
      const command = controlCommand(action);
      const delivery = await dispatch(command);
      return { command, delivery, response: responseFor(command, delivery) };
    },
    async task(command) { return dispatch(validateCommand(command)); },
  };
}
