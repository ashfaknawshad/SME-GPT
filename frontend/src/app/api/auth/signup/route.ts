import { prisma } from "@/lib/prisma";
import bcrypt from "bcryptjs";
import { NextResponse } from "next/server";

export async function POST(req: Request) {
  const body = await req.json();
  const { email, password, fullName, companyName } = body ?? {};

  if (typeof email !== "string" || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
    return NextResponse.json({ error: "Please enter a valid email address." }, { status: 400 });
  }
  if (typeof password !== "string" || password.length < 8) {
    return NextResponse.json({ error: "Password must be at least 8 characters." }, { status: 400 });
  }

  const existing = await prisma.user.findUnique({ where: { email } });

  if (existing) {
    return NextResponse.json({ error: "An account with this email already exists." }, { status: 409 });
  }

  const hashed = await bcrypt.hash(password, 10);

  const user = await prisma.user.create({
    data: {
      email,
      password: hashed,
      fullName,
      companyName,
    },
  });

  try {
    await prisma.activityLog.create({
      data: {
        userId: user.id,
        type: "SIGNUP",
        content: `New account created for ${user.email}`,
      },
    });
  } catch (logError) {
    console.error("SIGNUP AUDIT LOG ERROR:", logError);
  }

  // Never echo the stored record: it includes the bcrypt password hash.
  return NextResponse.json({
    user: { id: user.id, email: user.email, fullName: user.fullName, companyName: user.companyName },
  });
}