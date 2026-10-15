import type { MouseEvent, ReactNode } from "react";
import { navigate } from "../lib/router";

export function Link(props: {
  to: string;
  children: ReactNode;
  className?: string;
  "aria-current"?: "page" | undefined;
  onNavigate?: () => void;
}) {
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    props.onNavigate?.();
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
      return; // let the browser open new tabs/windows as usual
    }
    e.preventDefault();
    navigate(props.to);
  };
  return (
    <a
      href={props.to}
      className={props.className}
      onClick={onClick}
      aria-current={props["aria-current"]}
    >
      {props.children}
    </a>
  );
}
