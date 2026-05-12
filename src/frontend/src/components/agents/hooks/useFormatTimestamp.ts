import { useCallback } from 'react';

/**
 * Hook for formatting Date objects into readable date strings
 * @returns A function that formats Date objects
 */
export const useFormatTimestamp = (): ((date: Date | undefined) => string) => {
  return useCallback(
    (date: Date | undefined): string => {
      // Guard against Invalid Date — Intl.DateTimeFormat.format() throws
      // `RangeError: date value is not finite` on a NaN-time Date.
      if (!date || Number.isNaN(date.getTime())) {
        return '';
      }

      // Simple date formatting with time
      return new Intl.DateTimeFormat('en', {
        dateStyle: 'short',
        timeStyle: 'short'
      }).format(date);
    },
    [],
  );
};