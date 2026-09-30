export type User = { name: string; email: string };
export type Customer = { id: number; name: string; email: string; company: string; createdAt: string };
export type Product = { id: number; name: string; price: number };
export type CartLine = { productId: number; name: string; price: number; quantity: number };
export type Order = { id: string; items: CartLine[]; total: number; shipTo: string; createdAt: string };
export type Settings = { displayName: string; email: string; notifications: boolean };
