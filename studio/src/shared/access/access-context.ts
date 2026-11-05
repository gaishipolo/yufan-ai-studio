import { createContext } from 'react';
import { GUEST_SUBJECT, type Subject } from './types';

export const AccessContext = createContext<Subject>(GUEST_SUBJECT);
