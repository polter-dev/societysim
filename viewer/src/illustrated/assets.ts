const assets = import.meta.glob('./assets/**/*.png', { eager: true, query: '?url', import: 'default' }) as Record<string, string>;
export const art = (name: string) => assets[`./assets/${name}`];
