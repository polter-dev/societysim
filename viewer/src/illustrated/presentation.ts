import type { Event } from '../types/events';
import type { Scene } from '../scene/deriveScene';

export function eventPresentation(event: Event, scene: Scene) {
  const name = (id: string | null) => scene.players.find(p => p.id === id)?.name ?? id ?? 'Village';
  const title = event.type.replaceAll('_', ' ');
  const image = event.type === 'seer_check' ? '04_investigation.png' : event.type === 'doctor_save' ? '03_protection.png' : event.type === 'vote' ? '07_vote.png' : event.type.startsWith('wolf') || event.type === 'night_kill' ? '02_werewolf.png' : event.type === 'statement' ? '01_discussion.png' : '10_notes.png';
  return { title, image, actor: name(event.actor), target: event.target ? name(event.target) : null,
    text: event.public_text ?? (event.target ? `${name(event.actor)} → ${name(event.target)}` : title),
    result: event.meta.result ?? (event.type === 'doctor_save' ? event.meta.saved ? 'Target saved' : 'Protection did not stop a kill' : event.meta.winner ? `${event.meta.winner} won` : event.meta.reason ?? null) };
}

export function playerStatistics(scene: Scene, seat: string) {
  const mine = scene.log.filter(e => e.actor === seat);
  const death = scene.log.find(e => e.target === seat && (e.type === 'death_reveal' || e.type === 'elimination'));
  return { statements: mine.filter(e => e.type === 'statement').length, votes: mine.filter(e => e.type === 'vote').length,
    accused: scene.log.filter(e => e.type === 'accusation' && e.target === seat).length,
    days: Math.max(0, (death?.day ?? scene.day) - (scene.phase === 'night' && !death ? 1 : 0)), history: mine };
}
