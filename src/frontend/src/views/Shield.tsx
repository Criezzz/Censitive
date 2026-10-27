export function Shield({ small = false }: { small?: boolean }) {
  return (
    <svg
      width={small ? 18 : 25}
      height={small ? 18 : 25}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M12 3 4 6v6c0 4.4 4.8 7.9 8 9 3.2-1.1 8-4.6 8-9V6l-8-3Z"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path
        d="m8 12 2.5 2.5L16 9"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
