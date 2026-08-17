import { prisma } from "@/lib/prisma";
import bcrypt from "bcryptjs";
import { cookies, headers } from "next/headers";
import { NextResponse } from "next/server";
import * as jwt from "jsonwebtoken";
import crypto from "crypto";
import { generateDeviceToken, getDeviceName } from "@/lib/device";
import { sendLoginVerificationEmail } from "@/lib/mail";
import { resolveAppUrl } from "@/lib/appUrl";

export async function POST(req: Request) {
  try {
    const { email, password } = await req.json();

    if (!email || !password) {
      return NextResponse.json(
        { error: "Email and password are required." },
        { status: 400 }
      );
    }

    const user = await prisma.user.findUnique({
      where: { email },
    });

    if (!user) {
      return NextResponse.json(
        { error: "Invalid credentials" },
        { status: 401 }
      );
    }

    const valid = await bcrypt.compare(password, user.password);

    if (!valid) {
      return NextResponse.json(
        { error: "Invalid credentials" },
        { status: 401 }
      );
    }

    const cookieStore = await cookies();
    const headersList = await headers();

    let deviceToken = cookieStore.get("device_token")?.value;
    if (!deviceToken) {
      deviceToken = generateDeviceToken();
    }

    const userAgent = headersList.get("user-agent") || "unknown";
    const ipAddress = headersList.get("x-forwarded-for") || "unknown";
    const deviceName = getDeviceName(userAgent);

    if (user.twoFactorEnabled) {
      const trustedDevice = await prisma.trustedDevice.findFirst({
        where: {
          userId: user.id,
          deviceToken,
        },
      });

      if (!trustedDevice) {
        const verificationToken = crypto.randomBytes(32).toString("hex");
        const expiresAt = new Date(Date.now() + 1000 * 60 * 15);

        await prisma.loginVerification.create({
          data: {
            userId: user.id,
            token: verificationToken,
            deviceToken,
            deviceName,
            ipAddress,
            userAgent,
            expiresAt,
            trusted: false,
            approved: false,
            used: false,
          },
        });

        const appUrl = resolveAppUrl(req);
        const confirmLink = `${appUrl}/api/auth/confirm-login?token=${verificationToken}`;
        const trustLink = `${appUrl}/api/auth/trust-device?token=${verificationToken}`;

        await sendLoginVerificationEmail(user.email, confirmLink, trustLink);

        await prisma.activityLog.create({
          data: {
            userId: user.id,
            type: "LOGIN_2FA_PENDING",
            content: `2FA verification requested for ${deviceName}`,
            response: ipAddress,
          },
        });

        const response = NextResponse.json({
          requiresTwoFactor: true,
          message: "Verification email sent. Please confirm the login.",
          verificationToken,
        });

        response.cookies.set("device_token", deviceToken, {
          httpOnly: true,
          sameSite: "lax",
          secure: process.env.NODE_ENV === "production",
          path: "/",
          maxAge: 60 * 60 * 24 * 365,
        });

        return response;
      }

      await prisma.trustedDevice.update({
        where: { id: trustedDevice.id },
        data: {
          lastUsedAt: new Date(),
          ipAddress,
          userAgent,
          deviceName,
        },
      });
    }

    const token = jwt.sign(
      { userId: user.id, sessionVersion: user.sessionVersion, role: user.role },
      process.env.JWT_SECRET as string,
      { expiresIn: "7d" }
    );

    await prisma.activityLog.create({
      data: {
        userId: user.id,
        type: "LOGIN_SUCCESS",
        content: `Login success from ${deviceName}`,
        response: ipAddress,
      },
    });

    const response = NextResponse.json({
      success: true,
      token,
      user: {
        id: user.id,
        email: user.email,
        fullName: user.fullName,
        companyName: user.companyName,
      },
    });

    response.cookies.set("token", token, {
      httpOnly: true,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      maxAge: 60 * 60 * 24 * 7,
    });

    response.cookies.set("device_token", deviceToken, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 60 * 60 * 24 * 365,
    });

    return response;
  } catch (error) {
    console.error("Login error:", error);

    return NextResponse.json(
      { error: "Something went wrong during login." },
      { status: 500 }
    );
  }
}