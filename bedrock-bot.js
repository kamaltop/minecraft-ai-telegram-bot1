import 'dotenv/config';
import bedrock from 'bedrock-protocol';

const host = process.env.MC_HOST || 'localhost';
const port = Number(process.env.MC_PORT || 19132);
const username = process.env.MC_USERNAME || 'AFK_Bedrock_Bot';
const version = process.env.MC_BEDROCK_VERSION || '1.21.0';
const intervalMs = Number(process.env.AFK_INTERVAL_MS || 45000);

console.log(`[Bedrock] Connecting to ${host}:${port} as ${username}...`);

const client = bedrock.createClient({
  host,
  port,
  username,
  version,
  offline: process.env.MC_AUTH === 'offline'
});

client.on('join', () => console.log('[Bedrock] Connected. AFK routine started.'));
client.on('spawn', () => {
  let tick = 0;
  setInterval(() => {
    tick += 20;
    try {
      client.queue('player_auth_input', {
        pitch: 0,
        yaw: tick % 360,
        position: { x: 0, y: 0, z: 0 },
        move_vector: { x: 0, y: 0 },
        head_yaw: tick % 360,
        input_data: 0,
        input_mode: 3,
        play_mode: 0,
        interaction_model: 0,
        client_tick: tick,
        input_tick: tick,
        vehicle_rotation: { x: 0, y: 0 }
      });
    } catch (error) {
      console.error('[Bedrock] AFK packet error:', error.message);
    }
  }, intervalMs);
});

client.on('error', (error) => console.error('[Bedrock] Error:', error.message));
client.on('close', () => {
  console.log('[Bedrock] Disconnected. Restart the command to reconnect.');
  process.exitCode = 1;
});
