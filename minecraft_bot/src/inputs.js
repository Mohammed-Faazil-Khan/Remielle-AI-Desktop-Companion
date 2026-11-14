// Minecraft 1.21.3+ moved the walk/jump/sprint/sneak keys into their own "player_input" packet,
// apart from the position packets. Mineflayer 4.39 writes it only for sneak and for riding, so
// bot.setControlState('jump', true) never reaches the server: she performs the jump in her own
// physics, streams the rising positions, and Paper - never told she jumped - refuses the climb and
// leaves her hanging in the air. This mirrors her keys into that packet, like a real client does.
//
// The mirror is driven by setControlState itself, not only by the physics tick. The pathfinder owns
// the same keys and releases jump on every tick, so reading the state at tick time can miss a press
// that happened between two ticks - the jump then happens locally while the server still hears "not
// jumping", which is exactly the mid-air hang it causes.

const FLAGS = {
  forward: 'forward',
  back: 'backward',
  left: 'left',
  right: 'right',
  jump: 'jump',
  sprint: 'sprint',
  sneak: 'shift'
}

/** mineflayer's control names -> the player_input bitfield's flag names. */
export function inputsOf (controlState) {
  const inputs = {}
  for (const [control, flag] of Object.entries(FLAGS)) inputs[flag] = !!controlState[control]
  return inputs
}

const changed = (a, b) => !a || Object.values(FLAGS).some((flag) => a[flag] !== b[flag])

export function createInputSync ({ bot, log = () => {} }) {
  if (!bot.supportFeature('newPlayerInputPacket')) return { active: false, stop () {} }

  let lastSent = null
  let announced = false

  function publish () {
    let inputs
    try {
      inputs = inputsOf(bot.controlState)
    } catch (e) {
      return
    }
    // A client repeats its input every tick while a key is down; standing still needs no traffic.
    if (!changed(lastSent, inputs) && !Object.values(inputs).some(Boolean)) return
    lastSent = inputs
    if (!announced && inputs.jump) {
      announced = true
      log('reporting jumps to the server via player_input')
    }
    try {
      bot._client.write('player_input', { inputs })
    } catch (e) {
      log('player_input failed:', e.message)
    }
  }

  const original = typeof bot.setControlState === 'function' ? bot.setControlState : null
  if (original) {
    bot.setControlState = (control, state) => {
      original(control, state)
      publish() // same tick, before the pathfinder gets another turn at the keys
    }
  }
  bot.on('physicsTick', publish)

  return {
    active: true,
    stop () {
      if (original) bot.setControlState = original
      bot.removeListener('physicsTick', publish)
    }
  }
}
