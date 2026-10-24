import { Redirect } from "@/components/Redirect";

export default function Moved() {
  return <Redirect to="/assessor?tab=leaderboard" what="Compare" />;
}
