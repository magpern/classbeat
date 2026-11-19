import { useId, type ReactNode } from "react";
import { PageHeading } from "../ui/page-heading";

/**
 * The card of the focus pages (Sign in, Sign up and the mail-link pages): centred, narrow, 24 px
 * radius, with the Afterburn section heading (ADR 0013, A3) and an optional eyebrow above it.
 */
export function AuthCard({
  title,
  eyebrow,
  children,
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className="mx-auto w-full max-w-md rounded-lg border border-line bg-surface p-6 text-text shadow-card sm:p-8"
    >
      <div className="mb-5">
        <PageHeading
          size="section"
          eyebrow={eyebrow}
          title={title}
          titleId={id}
          titleClassName="leading-tight"
        />
      </div>
      {children}
    </section>
  );
}
