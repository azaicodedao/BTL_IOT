import { createContext, useContext, useEffect, useState } from "react";
import { session } from "./api";
import { startLive, stopLive } from "./live";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [auth, setAuth] = useState(session.get());
  useEffect(() => session.subscribe(setAuth), []);
  useEffect(() => {
    if (auth) startLive();
    else stopLive();
  }, [!!auth]);
  return <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const auth = useContext(AuthContext);
  const user = auth?.user;
  return {
    user,
    isAdmin: user?.role === "admin",
    canOperate: user?.role === "admin" || user?.role === "operator",
  };
}
